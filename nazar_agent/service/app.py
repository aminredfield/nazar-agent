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
from pathlib import Path
from datetime import date
from typing import Any, Literal

import httpx
from fastapi import FastAPI, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from nazar_agent.actions import ExecutionError, ProposalStore
from nazar_agent.engine import ToolExecutionError
from nazar_agent.onboarding import Draft, ImportRow, ParseError, PhotoRejected
from nazar_agent.assistant import Assistant, AssistantBusy
from nazar_agent.config import Settings, get_settings
from nazar_agent.nazar import NazarClient
from nazar_agent.telegram import Bot, TelegramApi
from nazar_agent.telegram.schedule import parse_brief_time, run_daily
from nazar_agent.views import audit_view, proposal_view
from nazar_agent.voice import transcribe

STATIC = Path(__file__).parent / "static"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
_ERROR_STATUS = {"not_found": 404, "wrong_status": 409, "refused": 409, "stale": 409, "failed": 502}


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    analyst: Literal["auto", "attendance", "timesheet"] = "auto"


class DecisionRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=80)


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
        nazar = NazarClient(
            base_url=active.nazar_base_url,
            email=active.nazar_email,
            password=active.nazar_password.get_secret_value(),
            timeout_seconds=active.nazar_timeout_seconds,
            transport=nazar_transport,
        )
        assistant = Assistant(active, nazar, ProposalStore(active.agent_db_path))
        app.state.assistant = assistant
        background, telegram = _start_telegram(active, assistant)
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
            await nazar.aclose()

    app = FastAPI(title="NAZAR Agent", lifespan=lifespan)

    def assistant() -> Assistant:
        return app.state.assistant

    @app.get("/", include_in_schema=False)
    async def window() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.post("/api/ask")
    async def ask(request: AskRequest) -> dict[str, Any]:
        try:
            answer = await assistant().ask(request.question, None if request.analyst == "auto" else request.analyst)
        except AssistantBusy:
            raise HTTPException(429, "The agent is already working on a question. Wait for it to finish.") from None
        return answer.view

    @app.get("/api/last")
    async def last() -> dict[str, Any] | None:
        """The last answer, with each proposal's status as it is now."""
        current = assistant()
        if current.last is None:
            return None
        view = current.last.view
        return {
            **view,
            "cards": [
                {**card, "actions": [proposal_view(current.store.get(action["proposal_id"])) for action in card["actions"]]}
                for card in view["cards"]
            ],
        }

    @app.post("/api/proposals/{proposal_id}/confirm")
    async def confirm(proposal_id: str, request: DecisionRequest) -> dict[str, Any]:
        return await _decide(assistant().executor.confirm(proposal_id, request.actor))

    @app.post("/api/proposals/{proposal_id}/decline")
    async def decline(proposal_id: str, request: DeclineRequest) -> dict[str, Any]:
        try:
            return proposal_view(assistant().executor.decline(proposal_id, request.actor, request.reason))
        except ExecutionError as exc:
            raise HTTPException(_ERROR_STATUS[exc.code], exc.message) from exc

    @app.post("/api/proposals/{proposal_id}/undo")
    async def undo(proposal_id: str, request: DecisionRequest) -> dict[str, Any]:
        return await _decide(assistant().executor.undo(proposal_id, request.actor))

    @app.get("/api/audit")
    async def audit(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        store = assistant().store
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
    async def read_import(text: str | None = Form(None), file: UploadFile | None = File(None)) -> dict[str, Any]:
        content = await _read_upload(file)
        try:
            rows, sites = await assistant().read_list(
                text=text, file=content, content_type=file.content_type if file else None
            )
        except ParseError as exc:
            raise HTTPException(422, str(exc)) from exc
        return {"rows": [_row_view(row) for row in rows], "sites": [{"id": site.id, "name": site.name} for site in sites]}

    @app.post("/api/import/drafts")
    async def create_drafts(request: DraftsRequest) -> list[dict[str, Any]]:
        current = assistant()
        sites = {site.id: site.name for site in await current.sites()}
        unknown = [row.site_id for row in request.rows if row.site_id and row.site_id not in sites]
        if unknown:
            raise HTTPException(422, f"Unknown site: {unknown[0]}")
        drafts = current.drafts.add_many(
            [(" ".join(row.full_name.split()), row.site_id, sites.get(row.site_id or "")) for row in request.rows],
            request.consent_signed_at,
            request.actor,
        )
        return [_draft_view(draft) for draft in drafts]

    @app.get("/api/import/drafts")
    async def waiting_drafts() -> list[dict[str, Any]]:
        return [_draft_view(draft) for draft in assistant().drafts.waiting()]

    @app.post("/api/import/drafts/{draft_id}/photo")
    async def photograph(draft_id: str, actor: str = Form(..., min_length=1, max_length=80), photo: UploadFile = File(...)) -> dict[str, Any]:
        current = assistant()
        content = await _read_upload(photo)
        try:
            draft = await current.drafts.photograph(draft_id, content or b"", photo.content_type or "image/jpeg", current.nazar, actor)
        except KeyError:
            raise HTTPException(404, "No such draft.") from None
        except PhotoRejected as exc:
            raise HTTPException(422, {"photo_rejected": exc.reasons}) from exc
        except ToolExecutionError as exc:
            # Mostly something a person can fix: consent date, a photo NAZAR rejects.
            raise HTTPException(422, str(exc)) from exc
        return _draft_view(draft)

    @app.post("/api/import/drafts/{draft_id}/discard")
    async def discard(draft_id: str, request: DecisionRequest) -> dict[str, Any]:
        drafts = assistant().drafts
        try:
            if not drafts.discard(draft_id, request.actor):
                raise HTTPException(409, "Only a person still waiting for a photo can be removed.")
            return _draft_view(drafts.get(draft_id))
        except KeyError:
            raise HTTPException(404, "No such draft.") from None

    return app


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
