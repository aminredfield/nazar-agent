"""The agent as an HTTP service with the assistant window.

    python -m nazar_agent.service      # http://127.0.0.1:8100

Ask a question → the team investigates, checks and recommends → each valid
action is stored as a pending proposal → the human applies, disagrees or
undoes, and every step lands in the audit trail.

There is no sign-in yet: the service binds to localhost. Opening it from the
NAZAR cabinet with a short-lived token is the next step.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import httpx
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from nazar_agent.actions import ExecutionError, Executor, ProposalStore
from nazar_agent.config import Settings, get_settings
from nazar_agent.findings import analyze
from nazar_agent.nazar import NazarClient
from nazar_agent.recommendations import Team, recommend
from nazar_agent.roles import attendance_analyst, timesheet_analyst
from nazar_agent.service.views import answer_view, audit_view, proposal_view
from nazar_agent.tools import attendance_tools, timesheet_tools
from nazar_agent.tools.common import COMPANY_TZ

STATIC = Path(__file__).parent / "static"
ANALYSTS = {
    "attendance": (attendance_analyst, attendance_tools),
    "timesheet": (timesheet_analyst, timesheet_tools),
}
_ERROR_STATUS = {"not_found": 404, "wrong_status": 409, "refused": 409, "stale": 409, "failed": 502}


class AskRequest(BaseModel):
    question: str = Field(min_length=3, max_length=500)
    analyst: Literal["attendance", "timesheet"] = "attendance"


class DecisionRequest(BaseModel):
    actor: str = Field(min_length=1, max_length=80)


class DeclineRequest(DecisionRequest):
    reason: str = Field(min_length=1, max_length=500)


@dataclass
class Runtime:
    settings: Settings
    nazar: NazarClient
    executor: Executor
    store: ProposalStore
    # One question at a time: each costs model tokens, and a double click
    # should not pay twice.
    asking: asyncio.Lock
    # The last answer, so reopening the window does not cost another question.
    last_answer: dict[str, Any] | None = None


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
        store = ProposalStore(active.agent_db_path)
        app.state.runtime = Runtime(active, nazar, Executor(store, nazar), store, asyncio.Lock())
        try:
            yield
        finally:
            await nazar.aclose()

    app = FastAPI(title="NAZAR Agent", lifespan=lifespan)

    def runtime() -> Runtime:
        return app.state.runtime

    @app.get("/", include_in_schema=False)
    async def window() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.post("/api/ask")
    async def ask(request: AskRequest) -> dict[str, Any]:
        state = runtime()
        if state.asking.locked():
            raise HTTPException(429, "The agent is already working on a question. Wait for it to finish.")
        async with state.asking:
            state.last_answer = await _answer(state, request)
            return state.last_answer

    @app.get("/api/last")
    async def last() -> dict[str, Any] | None:
        """The last answer, with each proposal's status as it is now."""
        state = runtime()
        if state.last_answer is None:
            return None
        return {
            **state.last_answer,
            "cards": [
                {**card, "actions": [proposal_view(state.store.get(action["proposal_id"])) for action in card["actions"]]}
                for card in state.last_answer["cards"]
            ],
        }

    @app.post("/api/proposals/{proposal_id}/confirm")
    async def confirm(proposal_id: str, request: DecisionRequest) -> dict[str, Any]:
        return await _decide(runtime().executor.confirm(proposal_id, request.actor))

    @app.post("/api/proposals/{proposal_id}/decline")
    async def decline(proposal_id: str, request: DeclineRequest) -> dict[str, Any]:
        try:
            return proposal_view(runtime().executor.decline(proposal_id, request.actor, request.reason))
        except ExecutionError as exc:
            raise HTTPException(_ERROR_STATUS[exc.code], exc.message) from exc

    @app.post("/api/proposals/{proposal_id}/undo")
    async def undo(proposal_id: str, request: DecisionRequest) -> dict[str, Any]:
        return await _decide(runtime().executor.undo(proposal_id, request.actor))

    @app.get("/api/audit")
    async def audit(limit: int = Query(50, ge=1, le=500)) -> list[dict[str, Any]]:
        store = runtime().store
        entries = store.audit(limit=limit)
        proposals = {}
        for entry in entries:
            if entry.proposal_id not in proposals:
                try:
                    proposals[entry.proposal_id] = store.get(entry.proposal_id)
                except KeyError:
                    proposals[entry.proposal_id] = None
        return [audit_view(entry, proposals[entry.proposal_id]) for entry in entries]

    return app


async def _answer(state: Runtime, request: AskRequest) -> dict[str, Any]:
    settings = state.settings
    analyst_llm = settings.analyst_llm()
    skeptic_llm = settings.skeptic_llm()
    make_role, make_tools = ANALYSTS[request.analyst]
    today = datetime.now(COMPANY_TZ).date()
    try:
        role = make_role(model=settings.analyst_model, tools=make_tools(state.nazar), today=today)
        analysis = await analyze(role, analyst_llm, request.question)
        team = Team(skeptic_llm, settings.skeptic_model, analyst_llm, settings.analyst_model)
        recommendations = await recommend(analysis, team, state.nazar, today)
    finally:
        await analyst_llm.aclose()
        await skeptic_llm.aclose()
    proposals = state.executor.propose(recommendations.cards, request.question)
    return answer_view(analysis, recommendations, proposals, settings.analyst_model)


async def _decide(outcome: Any) -> dict[str, Any]:
    try:
        return proposal_view(await outcome)
    except ExecutionError as exc:
        raise HTTPException(_ERROR_STATUS[exc.code], exc.message) from exc
