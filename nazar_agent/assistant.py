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
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from nazar_agent.actions import Executor, Proposal, ProposalStore
from nazar_agent.config import Settings
from nazar_agent.feedback import about, instructions_text, owner_feedback
from nazar_agent.findings import Analysis, analyze
from nazar_agent.nazar import NazarClient
from nazar_agent.onboarding import DraftStore, ImportRow, Site, read_list, review
from nazar_agent.progress import Progress, emit, tracking
from nazar_agent.recommendations import Recommendations, Team, recommend
from nazar_agent.roles import attendance_analyst, timesheet_analyst
from nazar_agent.roles.dispatcher import Analyst, route
from nazar_agent.tools import PhotoInspector, analyst_tools
from nazar_agent.tools.common import COMPANY_TZ, call_nazar
from nazar_agent.usage import Ledger, Meter, Source, metering
from nazar_agent.views import answer_view

ANALYSTS = {"attendance": attendance_analyst, "timesheet": timesheet_analyst}
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
    """One company's assistant: its proposals, drafts, audit trail and last answer.

    Every call names the NAZAR client to act with — the person who opened the
    window, or the service account of a local setup or the Telegram bot — so
    NAZAR checks that identity's rights and logs it.
    """

    def __init__(
        self,
        settings: Settings,
        store: ProposalStore,
        *,
        service: NazarClient | None = None,
        company: str = "service",
        ledger: Ledger | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.service = service
        # Whose model calls these are, in the ledger of what the agents spend.
        self.company = company
        self.ledger = ledger
        self.drafts = DraftStore(store.path, store)
        self.last: Answer | None = None
        # The question being answered, or the last one: the window follows it live.
        self.progress: Progress | None = None
        self._lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def executor(self, nazar: NazarClient | None = None) -> Executor:
        return Executor(self.store, self._acting(nazar))

    async def ask(
        self, question: str, analyst: Analyst | None = None, *, nazar: NazarClient | None = None, source: Source = "ask"
    ) -> Answer:
        if self._lock.locked():
            raise AssistantBusy
        async with self._lock:
            self.progress = Progress(question)
            meter = Meter()
            with tracking(self.progress), metering(meter):
                try:
                    self.last = await self._answer(question, analyst, self._acting(nazar), meter)
                except Exception as exc:
                    self.progress.finish(f"{type(exc).__name__}: {exc}")
                    raise
                finally:
                    # Tokens spent on a failed question are spent all the same.
                    self.spent(source, question, meter)
            self.progress.finish()
            return self.last

    def spent(self, source: Source, question: str, meter: Meter) -> None:
        if self.ledger is not None:
            self.ledger.record(company=self.company, source=source, question=question, calls=meter.calls)

    async def read_list(
        self, *, text: str | None, file: bytes | None, content_type: str | None, nazar: NazarClient | None = None
    ) -> tuple[list[ImportRow], list[Site]]:
        """A list of people → rows checked against the company's sites and employees."""
        acting = self._acting(nazar)
        llm = self.settings.analyst_llm()
        meter = Meter()
        try:
            with metering(meter):
                people = await read_list(llm, self.settings.analyst_model, text=text, file=file, content_type=content_type)
        finally:
            await llm.aclose()
            self.spent("import", text or "(file)", meter)
        sites = await self.sites(nazar=acting)
        employees = await call_nazar(acting.get("/employees"))
        return review(people, sites, employees), sites

    async def sites(self, *, nazar: NazarClient | None = None) -> list[Site]:
        objects = await call_nazar(self._acting(nazar).get("/objects", {"include_inactive": False}))
        return [Site(item["id"], item["name"], item.get("address") or "") for item in objects]

    def _acting(self, nazar: NazarClient | None) -> NazarClient:
        acting = nazar or self.service
        if acting is None:
            raise RuntimeError("No NAZAR identity to act with.")
        return acting

    async def _answer(self, question: str, analyst: Analyst | None, nazar: NazarClient, meter: Meter) -> Answer:
        settings = self.settings
        analyst_llm = settings.analyst_llm()
        skeptic_llm = settings.skeptic_llm()
        today = datetime.now(COMPANY_TZ).date()
        try:
            if analyst:
                emit("route", analyst=analyst, text="выбран в окне")
            chosen = analyst or await route(analyst_llm, settings.analyst_model, question)
            log.info("question routed to the %s analyst", chosen)
            # The photo inspector runs on the open-weights model, like the skeptic.
            photos = PhotoInspector(skeptic_llm, settings.skeptic_model)
            tools = analyst_tools(chosen, nazar, photos)
            # What the owner said when disagreeing before: the team does not bring it back as new.
            feedback = owner_feedback(self.store)
            role = ANALYSTS[chosen](model=settings.analyst_model, tools=tools, today=today, feedback=instructions_text(feedback))
            analysis = await analyze(role, analyst_llm, question)
            if photos.input_tokens:
                log.info("photo inspector: %d/%d tokens on %s", photos.input_tokens, photos.output_tokens, photos.model)
            team = Team(skeptic_llm, settings.skeptic_model, analyst_llm, settings.analyst_model)
            owner_said = {item.subject_id: about(feedback, item.subject_id) for item in feedback}
            recommendations = await recommend(analysis, team, nazar, today, owner_said)
        finally:
            await analyst_llm.aclose()
            await skeptic_llm.aclose()
        proposals = Executor(self.store, nazar).propose(recommendations.cards, question)
        view = answer_view(analysis, recommendations, proposals, meter.by_role())
        return Answer(question, chosen, analysis, recommendations, proposals, {**view, "analyst": chosen})


class Assistants:
    """One assistant per company, each with its own database file.

    Proposals, drafts and the audit trail of one company never share a file
    with another's. The service assistant (local setup, Telegram) keeps the
    configured file.
    """

    def __init__(self, settings: Settings, *, service: NazarClient | None = None, ledger: Ledger | None = None) -> None:
        self._settings = settings
        self._ledger = ledger
        self._by_company: dict[str, Assistant] = {}
        self.service = (
            Assistant(settings, ProposalStore(settings.agent_db_path), service=service, ledger=ledger)
            if service is not None
            else None
        )

    def for_company(self, company_id: str) -> Assistant:
        if company_id not in self._by_company:
            company = str(uuid.UUID(company_id))
            path = Path(self._settings.agent_db_path).with_name(f"agent-{company}.sqlite3")
            self._by_company[company_id] = Assistant(self._settings, ProposalStore(path), company=company, ledger=self._ledger)
        return self._by_company[company_id]
