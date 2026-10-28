"""The agent as one process: the assistant window, the Telegram bot and the morning brief.

    python -m nazar_agent.service      # http://127.0.0.1:8100

Ask a question → the team investigates, checks and recommends → each valid
action is stored as a pending proposal → a person applies, disagrees or
undoes — in the window or with Telegram buttons — and every step lands in the
same audit trail.

The window has no sign-in yet, so the service binds to localhost; opening it
from the NAZAR cabinet with a short-lived token comes with the NAZAR changes.
The bot answers only allowlisted chats.
"""

from __future__ import annotations

import asyncio
import contextlib
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
from nazar_agent.feedback import agreement
from nazar_agent.limits import LimitReached, Limits
from nazar_agent.llm.pricing import budget_usd
from nazar_agent.nazar import NazarClient
from nazar_agent.telegram import Bot, TelegramApi
from nazar_agent.telegram.schedule import parse_brief_time, run_daily
from nazar_agent.views import audit_view, proposal_view
from nazar_agent.voice import transcribe

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
# Reading a list is one short model call; its cost is not metered, so it counts as this.
IMPORT_COST_ESTIMATE_USD = 0.01
# A question that failed midway spent tokens nobody counted; for the demo budget it costs this.
FAILED_QUESTION_COST_USD = 0.10
_ERROR_STATUS = {"not_found": 404, "wrong_status": 409, "refused": 409, "stale": 409, "failed": 502}


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    analyst: Literal["auto", "attendance", "timesheet"] = "auto"


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
        service = None
        if active.has_service_account:
            service = NazarClient(
                base_url=active.nazar_base_url,
                email=active.nazar_email,
                password=active.nazar_password.get_secret_value(),
                agent_key=active.nazar_agent_key.get_secret_value(),
                timeout_seconds=active.nazar_timeout_seconds,
                transport=nazar_transport,
            )
        assistants = Assistants(active, service=service)
        sessions = Sessions(
            nazar_base_url=active.nazar_base_url,
            agent_key=active.nazar_agent_key.get_secret_value(),
            timeout_seconds=active.nazar_timeout_seconds,
            transport=nazar_transport,
        )
        app.state.settings, app.state.assistants, app.state.sessions = active, assistants, sessions
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

    async def caller(request: Request) -> AsyncIterator[Caller]:
        """Who acts: the person who opened the window from NAZAR, else (locally) the service account."""
        state = request.app.state
        session_id = request.cookies.get(COOKIE) or ""
        person = state.sessions.get(session_id)
        if person is not None:
            nazar = NazarClient(
                base_url=state.settings.nazar_base_url,
                access_token=person.access_token,
                timeout_seconds=state.settings.nazar_timeout_seconds,
                transport=nazar_transport,
            )
            try:
                yield Caller(
                    state.assistants.for_company(person.company_id), nazar, f"{person.full_name} (NAZAR)", person, session_id
                )
            finally:
                await nazar.aclose()
            return
        if state.assistants.service is not None and not state.settings.require_sign_in:
            yield Caller(state.assistants.service, state.assistants.service.service, "", None)
            return
        raise HTTPException(401, "Откройте помощника из кабинета NAZAR.")

    # The window's stylesheet, script and logo; the page itself is "/".
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
        return _person_view(current.person) if current.person else {"mode": "service"}

    @app.post("/api/ask")
    async def ask(http: Request, request: AskRequest, current: Caller = Depends(caller)) -> dict[str, Any]:
        analyst = None if request.analyst == "auto" else request.analyst
        if current.assistant.busy:
            raise HTTPException(429, "The agent is already working on a question. Wait for it to finish.")
        day = _reserve(http, current)
        cost = FAILED_QUESTION_COST_USD
        try:
            answer = await current.assistant.ask(request.question, analyst, nazar=current.nazar)
            cost = sum(budget_usd(item["model"], item["input_tokens"], item["output_tokens"]) for item in answer.view["stats"]["models"])
        except AssistantBusy:
            cost = 0.0
            raise HTTPException(429, "The agent is already working on a question. Wait for it to finish.") from None
        except ToolExecutionError as exc:
            raise HTTPException(502, str(exc)) from exc
        finally:
            _add_cost(http, current, day, cost)
        return answer.view

    @app.get("/api/progress")
    async def progress(after: int = Query(0, ge=0), current: Caller = Depends(caller)) -> dict[str, Any]:
        """What the team is doing on the current question, from event ``after`` on."""
        run = current.assistant.progress
        if run is None:
            return {"question": None, "running": False, "events": []}
        return {"question": run.question, "running": not run.finished, "events": run.since(after)}

    @app.get("/api/last")
    async def last(current: Caller = Depends(caller)) -> dict[str, Any] | None:
        """The last answer, with each proposal's status as it is now."""
        assistant = current.assistant
        if assistant.last is None:
            return None
        view = assistant.last.view
        return {
            **view,
            "cards": [
                {**card, "actions": [proposal_view(assistant.store.get(action["proposal_id"])) for action in card["actions"]]}
                for card in view["cards"]
            ],
        }

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

    @app.post("/api/import/read")
    async def read_import(
        http: Request,
        text: str | None = Form(None),
        file: UploadFile | None = File(None),
        current: Caller = Depends(caller),
    ) -> dict[str, Any]:
        content = await _read_upload(file)
        day = _reserve(http, current)
        _add_cost(http, current, day, IMPORT_COST_ESTIMATE_USD)
        try:
            rows, sites = await current.assistant.read_list(
                text=text, file=content, content_type=file.content_type if file else None, nazar=current.nazar
            )
        except ParseError as exc:
            raise HTTPException(422, str(exc)) from exc
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

    def actor_for(self, typed: str) -> str:
        """Who decided: from the NAZAR session, never from the window — a typed name is used only locally."""
        return self.name or typed.strip() or "Владелец"


def _reserve(request: Request, current: Caller) -> str:
    """Count the question before any model runs. Only a window opened from NAZAR is limited."""
    if current.person is None:
        return ""
    try:
        return request.app.state.limits.reserve(current.session_id)
    except LimitReached as exc:
        raise HTTPException(429, str(exc)) from None


def _add_cost(request: Request, current: Caller, day: str, cost_usd: float) -> None:
    if current.person is not None:
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

    async def voice_to_text(audio: bytes) -> str:
        llm = settings.analyst_llm()
        try:
            return await transcribe(llm, settings.voice_model, audio)
        finally:
            await llm.aclose()

    bot = Bot(api, assistant, settings.allowed_chat_ids, voice_to_text)
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
