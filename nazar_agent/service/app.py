"""The agent as one process: the assistant window, the Telegram bot and the morning brief.

    python -m nazar_agent.service      # http://127.0.0.1:8100

Ask a question → the team investigates, checks and recommends → each valid
action is stored as a pending proposal → a person applies, disagrees or
undoes — in the window or with Telegram buttons — and every step lands in the
same audit trail.

In production a person opens the window from the NAZAR cabinet with a
one-minute pass; locally the window acts as the service account. With
``DEMO_MODE`` it runs on the demo company in this process and opens at once
as its owner; every browser gets its own demo session for the limits. The bot
answers only allowlisted chats.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import mimetypes
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from datetime import date
from typing import Any, Literal

import httpx
from fastapi import Depends, FastAPI, File, Form, HTTPException, Query, Request, Response, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from nazar_agent.actions import ExecutionError
from nazar_agent.engine import ToolExecutionError
from nazar_agent.onboarding import Draft, ImportRow, ParseError, PhotoRejected
from nazar_agent.sessions import COOKIE, Person, SessionError, Sessions
from nazar_agent.assistant import Assistant, Assistants, AssistantBusy
from nazar_agent.config import Settings, get_settings
from nazar_agent.errors import owner_message
from nazar_agent.feedback import agreement
from nazar_agent.limits import LimitReached, Limits
from nazar_agent.usage import Ledger, NazarUsageReport
from nazar_agent.nazar import NazarClient
from nazar_agent.setup.apply import SetupRefused
from nazar_agent.setup.edits import EditError, SetupEdit
from nazar_agent.nazar.connect import NazarLink, connect
from nazar_agent.telegram import Bot, TelegramApi
from nazar_agent.telegram.schedule import parse_brief_time, run_daily
from nazar_agent.views import audit_view, proposal_view
from nazar_agent.voice import VoiceError

STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Reading a list is one short model call; its cost is not metered, so it counts as this.
IMPORT_COST_ESTIMATE_USD = 0.01
# Turning up to two minutes of speech into text: one short call, counted at this for the demo budget.
VOICE_COST_ESTIMATE_USD = 0.005
# A question that failed midway spent tokens nobody counted; for the demo budget it costs this.
FAILED_QUESTION_COST_USD = 0.10
# A demo browser's own session: the demo limits count questions per browser.
DEMO_COOKIE = "nazar_agent_demo"
_ERROR_STATUS = {"not_found": 404, "wrong_status": 409, "refused": 409, "stale": 409, "failed": 502}


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    analyst: Literal["auto", "attendance", "timesheet"] = "auto"
    # The window's language: failures are said in it, and an answer when the question's own is unclear.
    lang: Literal["ru", "uz", "en"] = "ru"


class SetupLinkRequest(BaseModel):
    draft_id: str = Field(max_length=40)
    url: str = Field(min_length=3, max_length=2000)


class SetupApplyRequest(BaseModel):
    draft_id: str = Field(max_length=40)
    actor: str = Field(default="", max_length=80)


class DecisionRequest(BaseModel):
    # Used only by a local setup; a person signed in from NAZAR is named by NAZAR.
    actor: str = Field(default="", max_length=80)


class SessionRequest(BaseModel):
    handoff: str = Field(min_length=20, max_length=4096)


class DeclineRequest(DecisionRequest):
    reason: str = Field(min_length=1, max_length=500)


class DraftRow(BaseModel):
    full_name: str = Field(min_length=2, max_length=120)
    site_id: str | None = None


class DraftsRequest(DecisionRequest):
    rows: list[DraftRow] = Field(min_length=1, max_length=300)
    # Biometric consent is signed on paper before the photo; NAZAR may require the date.
    consent_signed_at: date | None = None


def create_app(settings: Settings | None = None, *, nazar_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        active = settings or get_settings()
        link = connect(active, transport=nazar_transport)
        service = link.client() if link.has_service_account else None
        ledger = Ledger(Path(active.agent_db_path).with_name("usage.sqlite3"))
        reporter = NazarUsageReport(base_url=link.base_url, agent_key=link.agent_key, transport=link.transport)
        assistants = Assistants(active, service=service, ledger=ledger, reporter=reporter)
        sessions = Sessions(
            nazar_base_url=link.base_url,
            agent_key=link.agent_key,
            timeout_seconds=link.timeout_seconds,
            transport=link.transport,
        )
        app.state.settings, app.state.assistants, app.state.sessions = active, assistants, sessions
        app.state.link = link
        app.state.limits = Limits(
            Path(active.agent_db_path).with_name("usage.sqlite3"),
            questions_per_session=active.demo_questions_per_session,
            budget_usd_per_day=active.demo_budget_usd_per_day,
        )
        background, telegram = _start_telegram(active, assistants.service) if assistants.service else ([], None)
        try:
            yield
        finally:
            for task in background:
                task.cancel()
            for task in background:
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            if telegram is not None:
                await telegram.aclose()
            await sessions.aclose()
            if service is not None:
                await service.aclose()

    app = FastAPI(title="NAZAR Agent", lifespan=lifespan)

    async def caller(request: Request, response: Response) -> AsyncIterator[Caller]:
        """Who acts: the person who opened the window from NAZAR, else the service account (locally, in the demo)."""
        state = request.app.state
        session_id = request.cookies.get(COOKIE) or ""
        person = state.sessions.get(session_id)
        if person is not None:
            nazar = state.link.client(access_token=person.access_token)
            try:
                yield Caller(
                    state.assistants.for_company(person.company_id), nazar, f"{person.full_name} (NAZAR)", person, session_id
                )
            finally:
                await nazar.aclose()
            return
        if state.assistants.service is not None and state.settings.demo_mode:
            demo_session = request.cookies.get(DEMO_COOKIE) or ""
            if len(demo_session) < 20:
                demo_session = secrets.token_urlsafe(24)
                response.set_cookie(
                    DEMO_COOKIE, demo_session, httponly=True, samesite="strict", secure=state.settings.cookie_secure
                )
            visitor = state.assistants.for_visitor(demo_session)
            yield Caller(visitor, visitor.service, "", None, demo_session, demo=True)
            return
        if state.assistants.service is not None and not state.settings.window_needs_sign_in:
            yield Caller(state.assistants.service, state.assistants.service.service, "", None)
            return
        raise HTTPException(401, "Откройте помощника из кабинета NAZAR.")

    # The window's stylesheet, script and logo; the page itself is "/". A slim
    # image's Python does not know .xlsx: the Excel sample would go out untyped.
    mimetypes.add_type("application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", ".xlsx")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    async def window() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.post("/api/session")
    async def open_session(request: SessionRequest, response: Response) -> dict[str, Any]:
        """The cabinet's one-minute pass → this person's session in the window."""
        try:
            person = await app.state.sessions.open(request.handoff)
        except SessionError as exc:
            raise HTTPException(401, str(exc)) from exc
        response.set_cookie(
            COOKIE,
            person.session_id,
            httponly=True,
            samesite="strict",
            secure=app.state.settings.cookie_secure,
            expires=person.expires_at,
        )
        return _person_view(person)

    @app.get("/api/me")
    async def me(current: Caller = Depends(caller)) -> dict[str, Any]:
        if current.demo:
            return {"mode": "demo", "full_name": "Demo Owner", "company_name": "Mustahkam Qurilish", "role": "owner"}
        return _person_view(current.person) if current.person else {"mode": "service"}

    @app.post("/api/demo/reset")
    async def reset_demo(current: Caller = Depends(caller)) -> dict[str, Any]:
        """The demo company as it was this morning: every review, adjustment and new person dropped."""
        link: NazarLink = app.state.link
        if not current.demo or link.demo is None:
            raise HTTPException(404, "Not a demo.")
        if current.assistant.busy:
            raise HTTPException(429, "The agent is working on a question. Wait for it to finish.")
        link.demo.reset()
        current.assistant.forget()
        return {"reset": True, "now": link.demo.world.now.isoformat()}

    @app.post("/api/ask")
    async def ask(http: Request, request: AskRequest, current: Caller = Depends(caller)) -> dict[str, Any]:
        analyst = None if request.analyst == "auto" else request.analyst
        if current.assistant.busy:
            raise HTTPException(429, owner_message(AssistantBusy(), request.lang))
        day = _reserve(http, current, lang=request.lang)
        cost = FAILED_QUESTION_COST_USD
        try:
            answer = await current.assistant.ask(
                request.question, analyst, nazar=current.nazar, lang=request.lang, conversation=current.session_id
            )
            cost = answer.view["stats"]["cost_usd"]
        except AssistantBusy:
            cost = 0.0
            raise HTTPException(429, owner_message(AssistantBusy(), request.lang)) from None
        except Exception as exc:  # logged by the assistant; the owner reads what happened and what to do
            raise HTTPException(503, owner_message(exc, request.lang)) from exc
        finally:
            _add_cost(http, current, day, cost)
        return answer.view

    @app.get("/api/progress")
    async def progress(after: int = Query(0, ge=0), current: Caller = Depends(caller)) -> dict[str, Any]:
        """What the team is doing on the current question, from event ``after`` on."""
        run = current.assistant.progress
        if run is None:
            return {"run": None, "question": None, "running": False, "events": []}
        return {"run": run.id, "question": run.question, "running": not run.finished, "events": run.since(after)}

    @app.get("/api/last")
    async def last(current: Caller = Depends(caller)) -> dict[str, Any] | None:
        """The last answer, with each proposal's status as it is now."""
        assistant = current.assistant
        if assistant.last is None:
            return None
        return _fresh(assistant, assistant.last.view)

    @app.get("/api/dialog")
    async def dialog(current: Caller = Depends(caller)) -> list[dict[str, Any]]:
        """This window's conversation, oldest first: each question and its answer, proposals as they are now."""
        assistant = current.assistant
        return [
            {"question": turn.question, "meant": turn.meant, "answer": _fresh(assistant, turn.view)}
            for turn in assistant.dialogs.turns(current.session_id)
        ]

    @app.post("/api/dialog/reset")
    async def new_dialog(current: Caller = Depends(caller)) -> dict[str, bool]:
        """A new conversation: the next question is read on its own."""
        current.assistant.dialogs.reset(current.session_id)
        return {"ok": True}

    @app.post("/api/proposals/{proposal_id}/confirm")
    async def confirm(proposal_id: str, request: DecisionRequest, current: Caller = Depends(caller)) -> dict[str, Any]:
        executor = current.assistant.executor(current.nazar)
        return await _decide(executor.confirm(proposal_id, current.actor_for(request.actor)))

    @app.post("/api/proposals/{proposal_id}/decline")
    async def decline(proposal_id: str, request: DeclineRequest, current: Caller = Depends(caller)) -> dict[str, Any]:
        try:
            executor = current.assistant.executor(current.nazar)
            return proposal_view(executor.decline(proposal_id, current.actor_for(request.actor), request.reason))
        except ExecutionError as exc:
            raise HTTPException(_ERROR_STATUS[exc.code], exc.message) from exc

    @app.post("/api/proposals/{proposal_id}/undo")
    async def undo(proposal_id: str, request: DecisionRequest, current: Caller = Depends(caller)) -> dict[str, Any]:
        executor = current.assistant.executor(current.nazar)
        return await _decide(executor.undo(proposal_id, current.actor_for(request.actor)))

    @app.get("/api/stats")
    async def stats(current: Caller = Depends(caller)) -> dict[str, Any]:
        """How the owner decided on the proposals: the share applied is the quality measure."""
        decisions = current.assistant.store.decisions()
        return {**decisions, "agreement": agreement(decisions)}

    @app.get("/api/usage")
    async def usage(current: Caller = Depends(caller)) -> dict[str, Any]:
        """What this company's agents spent on models: today, this week, in all, by role and model."""
        assistant = current.assistant
        if assistant.ledger is None:
            return {}
        return assistant.ledger.summary(assistant.company)

    @app.get("/api/audit")
    async def audit(limit: int = Query(50, ge=1, le=500), current: Caller = Depends(caller)) -> list[dict[str, Any]]:
        store = current.assistant.store
        entries = store.audit(limit=limit)
        proposals: dict[str, Any] = {}
        for entry in entries:
            if entry.proposal_id not in proposals:
                try:
                    proposals[entry.proposal_id] = store.get(entry.proposal_id)
                except KeyError:
                    proposals[entry.proposal_id] = None
        return [audit_view(entry, proposals[entry.proposal_id]) for entry in entries]

    @app.post("/api/voice")
    async def voice(
        http: Request,
        audio: UploadFile = File(...),
        lang: Literal["ru", "uz", "en"] = Form("ru"),
        current: Caller = Depends(caller),
    ) -> dict[str, str]:
        """The window's microphone: speech becomes the question's text, which the owner reads before sending."""
        content = await _read_upload(audio)
        day = _reserve(http, current, question=False, lang=lang)
        _add_cost(http, current, day, VOICE_COST_ESTIMATE_USD)
        try:
            return {"text": await current.assistant.hear(content or b"", lang)}
        except VoiceError as exc:
            raise HTTPException(422, exc.said(lang)) from exc
        except Exception as exc:  # the model failed: what happened and what to do, in the window's language
            log.exception("voice failed")
            raise HTTPException(503, owner_message(exc, lang)) from exc

    # Setting the company up: the draft made in the chat, changed card by card, applied, undone.

    @app.get("/api/setup")
    async def setup_now(current: Caller = Depends(caller)) -> dict[str, Any]:
        """This window's setup draft with its plan, and the plan it applied last."""
        return await _setup(current.assistant.setup.view(current.session_id, current.nazar_or_service, _today(current)))

    @app.patch("/api/setup")
    async def setup_change(change: SetupEdit, current: Caller = Depends(caller)) -> dict[str, Any]:
        desk = current.assistant.setup
        return await _setup(desk.change(current.session_id, change, current.nazar_or_service, _today(current)))

    @app.post("/api/setup/link")
    async def setup_link(request: SetupLinkRequest, current: Caller = Depends(caller)) -> dict[str, Any]:
        """A map link pasted on the site's card: opened only on map hosts, its point put on the card."""
        desk = current.assistant.setup
        return await _setup(desk.point(current.session_id, request.draft_id, request.url, current.nazar_or_service, _today(current)))

    @app.post("/api/setup/apply")
    async def setup_apply(request: SetupApplyRequest, current: Caller = Depends(caller)) -> dict[str, Any]:
        desk = current.assistant.setup
        actor = current.actor_for(request.actor)
        return await _setup(desk.apply(current.session_id, request.draft_id, current.nazar_or_service, actor, _today(current)))

    @app.post("/api/setup/runs/{run_id}/undo")
    async def setup_undo(run_id: str, request: DecisionRequest, current: Caller = Depends(caller)) -> dict[str, Any]:
        desk = current.assistant.setup
        return await _setup(desk.undo(current.session_id, run_id, current.nazar_or_service, current.actor_for(request.actor)))

    @app.post("/api/setup/discard")
    async def setup_discard(current: Caller = Depends(caller)) -> dict[str, bool]:
        current.assistant.setup.discard(current.session_id)
        return {"ok": True}

    @app.get("/api/company")
    async def company_now(current: Caller = Depends(caller)) -> dict[str, Any]:
        try:
            return await current.assistant.company_now(nazar=current.nazar)
        except ToolExecutionError as exc:  # NAZAR refused: say so, not "Internal Server Error"
            raise HTTPException(502, str(exc)) from exc

    @app.post("/api/import/read")
    async def read_import(
        http: Request,
        text: str | None = Form(None),
        file: UploadFile | None = File(None),
        lang: Literal["ru", "uz", "en"] = Form("en"),
        current: Caller = Depends(caller),
    ) -> dict[str, Any]:
        content = await _read_upload(file)
        day = _reserve(http, current, lang=lang)
        _add_cost(http, current, day, IMPORT_COST_ESTIMATE_USD)
        try:
            rows, sites = await current.assistant.read_list(
                text=text, file=content, content_type=file.content_type if file else None, nazar=current.nazar, lang=lang
            )
        except ParseError as exc:
            raise HTTPException(422, exc.said(lang)) from exc
        except ToolExecutionError as exc:  # NAZAR refused: say so, not "Internal Server Error"
            raise HTTPException(502, str(exc)) from exc
        return {"rows": [_row_view(row) for row in rows], "sites": [{"id": site.id, "name": site.name} for site in sites]}

    @app.post("/api/import/drafts")
    async def create_drafts(request: DraftsRequest, current: Caller = Depends(caller)) -> list[dict[str, Any]]:
        sites = {site.id: site.name for site in await current.assistant.sites(nazar=current.nazar)}
        unknown = [row.site_id for row in request.rows if row.site_id and row.site_id not in sites]
        if unknown:
            raise HTTPException(422, f"Unknown site: {unknown[0]}")
        drafts = current.assistant.drafts.add_many(
            [(" ".join(row.full_name.split()), row.site_id, sites.get(row.site_id or "")) for row in request.rows],
            request.consent_signed_at,
            current.actor_for(request.actor),
        )
        return [_draft_view(draft) for draft in drafts]

    @app.get("/api/import/drafts")
    async def waiting_drafts(current: Caller = Depends(caller)) -> list[dict[str, Any]]:
        return [_draft_view(draft) for draft in current.assistant.drafts.waiting()]

    @app.post("/api/import/drafts/{draft_id}/photo")
    async def photograph(
        draft_id: str,
        actor: str = Form("", max_length=80),
        photo: UploadFile = File(...),
        current: Caller = Depends(caller),
    ) -> dict[str, Any]:
        content = await _read_upload(photo)
        try:
            draft = await current.assistant.drafts.photograph(
                draft_id, content or b"", photo.content_type or "image/jpeg", current.nazar, current.actor_for(actor)
            )
        except KeyError:
            raise HTTPException(404, "No such draft.") from None
        except PhotoRejected as exc:
            raise HTTPException(422, {"photo_rejected": exc.reasons}) from exc
        except ToolExecutionError as exc:
            # Mostly something a person can fix: consent date, a photo NAZAR rejects.
            raise HTTPException(422, str(exc)) from exc
        return _draft_view(draft)

    @app.post("/api/import/drafts/{draft_id}/discard")
    async def discard(draft_id: str, request: DecisionRequest, current: Caller = Depends(caller)) -> dict[str, Any]:
        drafts = current.assistant.drafts
        try:
            if not drafts.discard(draft_id, current.actor_for(request.actor)):
                raise HTTPException(409, "Only a person still waiting for a photo can be removed.")
            return _draft_view(drafts.get(draft_id))
        except KeyError:
            raise HTTPException(404, "No such draft.") from None

    return app


@dataclass(frozen=True, slots=True)
class Caller:
    assistant: Assistant
    nazar: NazarClient | None
    # The name NAZAR knows the person by; empty for the local service account.
    name: str
    person: Person | None
    session_id: str = ""  # the window session, for the demo limits
    demo: bool = False  # the demo company in this process

    @property
    def nazar_or_service(self) -> NazarClient:
        """Who acts in NAZAR: the person who opened the window, or the local setup's service account."""
        acting = self.nazar or self.assistant.service
        if acting is None:
            raise HTTPException(503, "No NAZAR account to act with.")
        return acting

    def actor_for(self, typed: str) -> str:
        """Who decided: from the NAZAR session, never from the window — a typed name is used only locally."""
        return self.name or typed.strip() or "Owner"


async def _setup(outcome: Any) -> dict[str, Any]:
    """A setup call's answer, or what went wrong in words: 422 a change to fix, 409 not now, 502 NAZAR refused."""
    try:
        return await outcome
    except EditError as exc:
        raise HTTPException(422, str(exc)) from exc
    except SetupRefused as exc:
        raise HTTPException(409, str(exc)) from exc
    except KeyError:
        raise HTTPException(404, "No such plan.") from None
    except ToolExecutionError as exc:
        raise HTTPException(502, str(exc)) from exc


def _today(current: Caller) -> date:
    return current.assistant.settings.now().date()


def _fresh(assistant: Assistant, view: dict[str, Any]) -> dict[str, Any]:
    """An answer shown again: each proposal with its status as it is now."""
    return {
        **view,
        "cards": [
            {**card, "actions": [proposal_view(assistant.store.get(action["proposal_id"])) for action in card["actions"]]}
            for card in view["cards"]
        ],
    }


def _reserve(request: Request, current: Caller, *, question: bool = True, lang: str = "en") -> str:
    """Count the question before any model runs. A window opened from NAZAR and a demo browser are limited.

    ``question=False`` — a model call that is not a question (the microphone): checked against the limits, not counted.
    """
    if current.person is None and not current.demo:
        return ""
    try:
        return request.app.state.limits.reserve(current.session_id, question=question)
    except LimitReached as exc:
        raise HTTPException(429, exc.said(lang)) from None


def _add_cost(request: Request, current: Caller, day: str, cost_usd: float) -> None:
    if current.person is not None or current.demo:
        request.app.state.limits.add_cost(current.session_id, day, cost_usd)


def _person_view(person: Person) -> dict[str, Any]:
    return {
        "mode": "person",
        "full_name": person.full_name,
        "company_name": person.company_name,
        "role": person.role,
        "expires_at": person.expires_at.isoformat(),
    }


async def _read_upload(file: UploadFile | None) -> bytes | None:
    if file is None:
        return None
    content = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "The file is larger than 10 MB.")
    return content


def _row_view(row: ImportRow) -> dict[str, Any]:
    return {
        "full_name": row.full_name,
        "as_written": row.as_written,
        "site_hint": row.site_hint,
        "site_id": row.site_id,
        "issues": list(row.issues),
    }


def _draft_view(draft: Draft) -> dict[str, Any]:
    return {
        "id": draft.id,
        "status": draft.status,
        "full_name": draft.full_name,
        "site_name": draft.site_name,
        "employee_id": draft.employee_id,
    }


def _start_telegram(settings: Settings, assistant: Assistant) -> tuple[list[asyncio.Task[None]], TelegramApi | None]:
    """The bot and the morning brief, when a bot token is configured."""
    if settings.telegram_bot_token is None or not settings.telegram_bot_token.get_secret_value():
        return [], None
    api = TelegramApi(settings.telegram_bot_token.get_secret_value(), proxy=settings.telegram_proxy)

    bot = Bot(api, assistant, settings.allowed_chat_ids, assistant.hear)
    tasks = [asyncio.create_task(bot.run())]
    brief_at = parse_brief_time(settings.brief_time)
    if brief_at is not None:
        tasks.append(asyncio.create_task(run_daily(brief_at, lambda: bot.brief(sorted(settings.allowed_chat_ids)))))
    return tasks, api


async def _decide(outcome: Any) -> dict[str, Any]:
    try:
        return proposal_view(await outcome)
    except ExecutionError as exc:
        raise HTTPException(_ERROR_STATUS[exc.code], exc.message) from exc
