"""The assistant: one question in, cards with pending proposals out.

Shared by every way to reach the agent — the window, Telegram, the morning
brief — so they all run the same team, store proposals in the same place and
write to the same audit trail.

    question → dispatcher → analyst → fact checker → skeptic →
    recommendation writer → validated actions stored as pending proposals

The dispatcher answers a greeting, something off-topic or forbidden, or the
future itself, with a fixed short text: no analyst, no NAZAR reads. A name
that fits several people, or no one, is asked back with buttons
(``nazar_agent.clarify``).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from nazar_agent.actions import Executor, Proposal, ProposalStore
from nazar_agent.clarify import Choice, Clarification, resolve
from nazar_agent.config import Settings
from nazar_agent.engine import RoleRun
from nazar_agent.errors import WindowLang, owner_message
from nazar_agent.feedback import about, instructions_text, owner_feedback
from nazar_agent.findings import Analysis, AnalystReport, analyze
from nazar_agent.nazar import NazarClient
from nazar_agent.onboarding import DraftStore, ImportRow, Site, read_list, review
from nazar_agent.progress import Progress, emit, tracking
from nazar_agent.recommendations import Recommendations, Team, recommend
from nazar_agent.replies import clarification, reply, suggestions
from nazar_agent.roles import attendance_analyst, timesheet_analyst
from nazar_agent.roles.dispatcher import LANGUAGES, Analyst, Request, parse
from nazar_agent.tools import PhotoInspector, analyst_tools
from nazar_agent.tools.common import call_nazar
from nazar_agent.usage import Ledger, Meter, NazarUsageReport, Source, metering
from nazar_agent.views import answer_view
from nazar_agent.voice import transcribe

ANALYSTS = {"attendance": attendance_analyst, "timesheet": timesheet_analyst}
MAX_VISITORS = 500  # demo browsers kept in memory; the oldest idle one goes first
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
        reporter: NazarUsageReport | None = None,
    ) -> None:
        self.settings = settings
        self.store = store
        self.service = service
        # Whose model calls these are, in the ledger of what the agents spend.
        self.company = company
        self.ledger = ledger
        self.reporter = reporter
        self.drafts = DraftStore(store.path, store)
        self.last: Answer | None = None
        # The question being answered, or the last one: the window follows it live.
        self.progress: Progress | None = None
        self._lock = asyncio.Lock()

    @property
    def busy(self) -> bool:
        return self._lock.locked()

    def forget(self) -> None:
        """Drop the last answer and its live feed, e.g. after the demo company was reset."""
        self.last = None
        self.progress = None

    def executor(self, nazar: NazarClient | None = None) -> Executor:
        return Executor(self.store, self._acting(nazar))

    async def ask(
        self,
        question: str,
        analyst: Analyst | None = None,
        *,
        nazar: NazarClient | None = None,
        source: Source = "ask",
        lang: WindowLang = "ru",
    ) -> Answer:
        """``lang`` — the window's language: failures are said in it, and the answer when the question's is unclear."""
        if self._lock.locked():
            raise AssistantBusy
        async with self._lock:
            self.progress = Progress(question)
            meter = Meter()
            with tracking(self.progress), metering(meter):
                try:
                    self.last = await self._answer(question, analyst, self._acting(nazar), meter, lang)
                except Exception as exc:
                    log.exception("answering %r failed", question)
                    self.progress.finish(owner_message(exc, lang))
                    raise
                finally:
                    # Tokens spent on a failed question are spent all the same.
                    await self.spent(source, question, meter)
            self.progress.finish()
            return self.last

    async def hear(self, audio: bytes, lang: str | None = None) -> str:
        """A voice message — the bot's or the window's microphone — as text, in the language it was said in.

        ``lang`` — the window's language, a hint for the model.
        """
        llm = self.settings.analyst_llm()
        meter = Meter()
        try:
            with metering(meter):
                return await transcribe(llm, self.settings.voice_model, audio, lang)
        finally:
            await llm.aclose()
            await self.spent("voice", "(voice message)", meter)

    async def spent(self, source: Source, question: str, meter: Meter) -> None:
        """What a run spent: into the local ledger and, for the superadmin, into NAZAR."""
        if self.ledger is not None:
            self.ledger.record(company=self.company, source=source, question=question, calls=meter.calls)
        if self.reporter is not None:
            # A window's company is known; the bot's service account learns it when it signs in.
            company = self.company if self.company != "service" else (getattr(self.service, "company_id", None) or "")
            await self.reporter.send(company_id=company, source=source, calls=meter.calls)

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
            sites = await self.sites(nazar=acting)
            employees = await call_nazar(acting.get("/employees"))
        finally:
            await llm.aclose()
            # After NAZAR was asked: by then the bot's service account knows its company.
            await self.spent("import", text or "(file)", meter)
        return review(people, sites, employees), sites

    async def sites(self, *, nazar: NazarClient | None = None) -> list[Site]:
        objects = await call_nazar(self._acting(nazar).get("/objects", {"include_inactive": False}))
        return [Site(item["id"], item["name"], item.get("address") or "") for item in objects]

    def _acting(self, nazar: NazarClient | None) -> NazarClient:
        acting = nazar or self.service
        if acting is None:
            raise RuntimeError("No NAZAR identity to act with.")
        return acting

    async def _answer(
        self, question: str, analyst: Analyst | None, nazar: NazarClient, meter: Meter, lang: WindowLang = "ru"
    ) -> Answer:
        settings = self.settings
        analyst_llm = settings.analyst_llm()
        skeptic_llm = settings.skeptic_llm()
        now = settings.now()
        today = now.date()
        try:
            request = await parse(analyst_llm, settings.analyst_model, question, today, lang)
            if request.short:
                log.info("the dispatcher answered a %s request itself", request.intent)
                return short_answer(question, request, meter)
            found = await resolve(request, question, nazar)
            if isinstance(found, Clarification):
                text = clarification(found, request.lang)
                emit("reply", intent="clarify", text=text)
                return short_answer(question, replace(request, intent="clarify"), meter, text=text, choices=found.choices)
            request = found
            if analyst:
                emit("route", analyst=analyst, text="выбран в окне")
            chosen = analyst or request.analyst
            log.info("question routed to the %s analyst", chosen)
            # The photo inspector runs on the open-weights model, like the skeptic.
            photos = PhotoInspector(skeptic_llm, settings.skeptic_model)
            tools = analyst_tools(chosen, nazar, photos, now)
            # What the owner said when disagreeing before: the team does not bring it back as new.
            feedback = owner_feedback(self.store)
            role = ANALYSTS[chosen](
                model=settings.analyst_model, tools=tools, today=today, feedback=instructions_text(feedback), request=request.hints()
            )
            analysis = await analyze(role, analyst_llm, question, language=LANGUAGES[request.lang])
            if photos.input_tokens:
                log.info("photo inspector: %d/%d tokens on %s", photos.input_tokens, photos.output_tokens, photos.model)
            team = Team(skeptic_llm, settings.skeptic_model, analyst_llm, settings.analyst_model)
            owner_said = {item.subject_id: about(feedback, item.subject_id) for item in feedback}
            recommendations = await recommend(analysis, team, nazar, today, owner_said, question)
        finally:
            await analyst_llm.aclose()
            await skeptic_llm.aclose()
        proposals = Executor(self.store, nazar).propose(recommendations.cards, question, request.lang)
        view = answer_view(analysis, recommendations, proposals, meter.by_role())
        return Answer(question, chosen, analysis, recommendations, proposals, {**view, "analyst": chosen, "choices": []})


def short_answer(
    question: str, request: Request, meter: Meter, *, text: str | None = None, choices: tuple[Choice, ...] | None = None
) -> Answer:
    """The dispatcher's own answer, shaped like any other: a summary, no cards, questions to press."""
    text = text or reply(request)
    choices = suggestions(request) if choices is None else choices
    run = RoleRun(role="dispatcher", answer=text, steps=(), model_turns=0, input_tokens=0, output_tokens=0, stopped="answered", conversation=())
    analysis = Analysis(
        run=run, report=AnalystReport(answer=[], findings=[], data_gaps=[], summary=text), rejected=(), corrections=0,
        clean_first_time=True, caught=(), input_tokens=0, output_tokens=0,
    )
    recommendations = Recommendations(cards=(), notes=(), usage=())
    view = answer_view(analysis, recommendations, [], meter.by_role())
    shown = {**view, "analyst": request.analyst, "reply": request.intent, "choices": [asdict(choice) for choice in choices]}
    return Answer(question, request.analyst, analysis, recommendations, [], shown)


class Assistants:
    """One assistant per company, each with its own database file.

    Proposals, drafts and the audit trail of one company never share a file
    with another's. The service assistant (local setup, Telegram) keeps the
    configured file.
    """

    def __init__(
        self,
        settings: Settings,
        *,
        service: NazarClient | None = None,
        ledger: Ledger | None = None,
        reporter: NazarUsageReport | None = None,
    ) -> None:
        self._settings = settings
        self._ledger = ledger
        self._reporter = reporter
        self._by_company: dict[str, Assistant] = {}
        self._visitors: dict[str, Assistant] = {}
        self.service = (
            Assistant(settings, ProposalStore(settings.agent_db_path), service=service, ledger=ledger, reporter=reporter)
            if service is not None
            else None
        )

    def for_visitor(self, session_id: str) -> Assistant:
        """A demo browser's own assistant: its own question at a time and last answer.

        Visitors share the demo company, its proposals and audit trail, but one
        visitor's question does not keep another waiting.
        """
        if self.service is None:
            raise RuntimeError("a demo visitor needs the service account")
        if session_id not in self._visitors:
            if len(self._visitors) >= MAX_VISITORS:
                idle = next((key for key, item in self._visitors.items() if not item.busy), None)
                if idle is not None:
                    del self._visitors[idle]  # the oldest idle one: dicts keep insertion order
            self._visitors[session_id] = Assistant(
                self._settings, self.service.store, service=self.service.service, ledger=self._ledger, reporter=self._reporter
            )
        return self._visitors[session_id]

    def for_company(self, company_id: str) -> Assistant:
        if company_id not in self._by_company:
            company = str(uuid.UUID(company_id))
            path = Path(self._settings.agent_db_path).with_name(f"agent-{company}.sqlite3")
            self._by_company[company_id] = Assistant(
                self._settings, ProposalStore(path), company=company, ledger=self._ledger, reporter=self._reporter
            )
        return self._by_company[company_id]
