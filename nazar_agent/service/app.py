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
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from nazar_agent.actions import ExecutionError, ProposalStore
from nazar_agent.assistant import Assistant, AssistantBusy
from nazar_agent.config import Settings, get_settings
from nazar_agent.nazar import NazarClient
from nazar_agent.telegram import Bot, TelegramApi
from nazar_agent.telegram.schedule import parse_brief_time, run_daily
from nazar_agent.views import audit_view, proposal_view
from nazar_agent.voice import transcribe

STATIC = Path(__file__).parent / "static"
_ERROR_STATUS = {"not_found": 404, "wrong_status": 409, "refused": 409, "stale": 409, "failed": 502}


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    analyst: Literal["auto", "attendance", "timesheet"] = "auto"


class DecisionRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=80)


class DeclineRequest(DecisionRequest):
    reason: str = Field(min_length=1, max_length=500)


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

    return app


def _start_telegram(settings: Settings, assistant: Assistant) -> tuple[list[asyncio.Task[None]], TelegramApi | None]:
    """The bot and the morning brief, when a bot token is configured."""
    if settings.telegram_bot_token is None or not settings.telegram_bot_token.get_secret_value():
        return [], None
    api = TelegramApi(settings.telegram_bot_token.get_secret_value())

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
