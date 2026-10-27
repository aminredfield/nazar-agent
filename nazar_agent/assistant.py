"""The assistant: one question in, cards with pending proposals out.

Shared by every way to reach the agent — the window, Telegram, the morning
brief — so they all run the same team, store proposals in the same place and
write to the same audit trail.

    question → dispatcher (unless the analyst is given) → analyst → fact
    checker → skeptic → recommendation writer → validated actions stored as
    pending proposals
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from nazar_agent.actions import Executor, Proposal, ProposalStore
from nazar_agent.config import Settings
from nazar_agent.findings import Analysis, analyze
from nazar_agent.nazar import NazarClient
from nazar_agent.onboarding import DraftStore, ImportRow, Site, read_list, review
from nazar_agent.recommendations import Recommendations, Team, recommend
from nazar_agent.roles import attendance_analyst, timesheet_analyst
from nazar_agent.roles.dispatcher import Analyst, route
from nazar_agent.tools import attendance_tools, timesheet_tools
from nazar_agent.tools.common import COMPANY_TZ, call_nazar
from nazar_agent.views import answer_view

ANALYSTS = {
    "attendance": (attendance_analyst, attendance_tools),
    "timesheet": (timesheet_analyst, timesheet_tools),
}
log = logging.getLogger(__name__)


class AssistantBusy(Exception):
    """Another question is being answered; each costs model tokens, so one at a time."""


@dataclass(frozen=True, slots=True)
class Answer:
    question: str
    analyst: Analyst
    analysis: Analysis
    recommendations: Recommendations
    proposals: list[list[Proposal]]  # per card, in card order
    view: dict[str, Any]


class Assistant:
    def __init__(self, settings: Settings, nazar: NazarClient, store: ProposalStore) -> None:
        self.settings = settings
        self.nazar = nazar
        self.store = store
        self.executor = Executor(store, nazar)
        self.drafts = DraftStore(settings.agent_db_path, store)
        self.last: Answer | None = None
        self._lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    async def ask(self, question: str, analyst: Analyst | None = None) -> Answer:
        if self._lock.locked():
            raise AssistantBusy
        async with self._lock:
            self.last = await self._answer(question, analyst)
            return self.last

    async def read_list(
        self, *, text: str | None, file: bytes | None, content_type: str | None
    ) -> tuple[list[ImportRow], list[Site]]:
        """A list of people → rows checked against the company's sites and employees."""
        llm = self.settings.analyst_llm()
        try:
            people = await read_list(llm, self.settings.analyst_model, text=text, file=file, content_type=content_type)
        finally:
            await llm.aclose()
        sites = await self.sites()
        employees = await call_nazar(self.nazar.get("/employees"))
        return review(people, sites, employees), sites

    async def sites(self) -> list[Site]:
        objects = await call_nazar(self.nazar.get("/objects", {"include_inactive": False}))
        return [Site(item["id"], item["name"], item.get("address") or "") for item in objects]

    async def _answer(self, question: str, analyst: Analyst | None) -> Answer:
        settings = self.settings
        analyst_llm = settings.analyst_llm()
        skeptic_llm = settings.skeptic_llm()
        today = datetime.now(COMPANY_TZ).date()
        try:
            chosen = analyst or await route(analyst_llm, settings.analyst_model, question)
            log.info("question routed to the %s analyst", chosen)
            make_role, make_tools = ANALYSTS[chosen]
            role = make_role(model=settings.analyst_model, tools=make_tools(self.nazar), today=today)
            analysis = await analyze(role, analyst_llm, question)
            team = Team(skeptic_llm, settings.skeptic_model, analyst_llm, settings.analyst_model)
            recommendations = await recommend(analysis, team, self.nazar, today)
        finally:
            await analyst_llm.aclose()
            await skeptic_llm.aclose()
        proposals = self.executor.propose(recommendations.cards, question)
        view = answer_view(analysis, recommendations, proposals, settings.analyst_model)
        return Answer(question, chosen, analysis, recommendations, proposals, {**view, "analyst": chosen})
