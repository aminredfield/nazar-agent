"""From verified findings to recommendation cards.

    verified report → skeptic (other model) → confidence by code
                    → recommendation writer → actions validated by code

A card is what the owner sees: facts with sources, a possible explanation,
what else could explain it (the skeptic first), how confident the team is,
how to check, and actions that already passed validation, each with NAZAR's
own "before → after" when it changes pay.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import date

from nazar_agent.findings import Analysis, Evidence, Finding
from nazar_agent.findings.models import Confidence
from nazar_agent.llm import LLMClient
from nazar_agent.nazar import NazarClient
from nazar_agent.progress import emit
from nazar_agent.recommendations.catalog import CATALOG, Context, ValidatedAction, validate_action
from nazar_agent.recommendations.composer import CaseInput, CasePlan, compose
from nazar_agent.recommendations.skeptic import SkepticView, merge_confidence, run_skeptic

_ORDER: dict[Confidence, int] = {"high": 0, "medium": 1, "low": 2}
MAX_ALTERNATIVES = 3  # a card is read by a busy owner, not audited
log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Card:
    finding: Finding
    confidence: Confidence
    analyst_confidence: Confidence
    skeptic: SkepticView | None
    alternatives: tuple[str, ...]
    check_steps: tuple[str, ...]
    actions: tuple[ValidatedAction, ...]
    rejected_actions: tuple[ValidatedAction, ...]
    actions_why: str  # the writer's reason for these actions, or for none
    owner_said: tuple[str, ...] = ()  # the owner's earlier reasons to disagree about this subject


@dataclass(frozen=True, slots=True)
class Usage:
    role: str
    model: str
    input_tokens: int
    output_tokens: int


@dataclass(frozen=True, slots=True)
class Recommendations:
    cards: tuple[Card, ...]
    notes: tuple[str, ...]  # skeptic or writer problems the owner should know about
    usage: tuple[Usage, ...]


@dataclass(frozen=True, slots=True)
class Team:
    """The models behind the review, so each can run on its own provider."""

    skeptic_llm: LLMClient
    skeptic_model: str
    writer_llm: LLMClient
    writer_model: str


async def recommend(
    analysis: Analysis,
    team: Team,
    nazar: NazarClient,
    today: date,
    owner_said: Mapping[str, tuple[str, ...]] | None = None,
    question: str = "",
) -> Recommendations:
    """``owner_said`` — the owner's earlier reasons to disagree, by person or site id;
    ``question`` — what the owner asked, for the values of an action they spelled out."""
    findings = analysis.report.findings
    evidence = Evidence.from_steps(analysis.run.steps)
    owner_said = owner_said or {}

    log.info("skeptic: %d finding(s) on %s", len(findings), team.skeptic_model)
    emit("skeptic", status="start", model=team.skeptic_model, cases=len(findings))
    skeptic = await run_skeptic(team.skeptic_llm, team.skeptic_model, analysis.report, evidence, owner_said)
    emit(
        "skeptic",
        status="done",
        model=team.skeptic_model,
        verdicts=[
            {
                "title": finding.title,
                "subject": finding.subject_name,
                "verdict": view.verdict if (view := skeptic.views.get(index)) else None,
            }
            for index, finding in enumerate(findings)
        ],
        text=skeptic.error or "",
    )
    cases = [
        CaseInput(finding, merge_confidence(finding.confidence, skeptic.views.get(index)), skeptic.views.get(index))
        for index, finding in enumerate(findings)
    ]
    log.info("writer: recommendations on %s", team.writer_model)
    emit("writer", status="start", model=team.writer_model, cases=len(cases))
    names = record_names(evidence)
    writer = await compose(team.writer_llm, team.writer_model, cases, today, question, names=names)
    emit("writer", status="done", model=team.writer_model, actions=sum(len(plan.actions) for plan in writer.plans.values()))
    plans = dict(writer.plans)
    checked = {index: await _validated(plan, cases[index], evidence, nazar) for index, plan in plans.items()}
    writer_tokens = [writer.input_tokens, writer.output_tokens]

    # A field left empty or an id from nowhere goes back to the writer once, like a report to the analyst;
    # a policy refusal does not: asking again would only invite a weaker variant of the same action.
    fixable = [
        f"case {index + 1}, {action.name}: {'; '.join(action.problems)}"
        for index, actions in checked.items()
        for action in actions
        if action.fixable
    ]
    if fixable:
        emit("writer", status="start", model=team.writer_model, cases=len(cases), retry=True)
        again = await compose(team.writer_llm, team.writer_model, cases, today, question, corrections=fixable, names=names)
        writer_tokens = [writer_tokens[0] + again.input_tokens, writer_tokens[1] + again.output_tokens]
        retried = {index for index, actions in checked.items() if any(action.fixable for action in actions)}
        for index in retried & again.plans.keys():
            plans[index] = again.plans[index]
            fresh = await _validated(plans[index], cases[index], evidence, nazar)
            # What passed the first time stays, unless the second plan acts on the same record again.
            kept = [action for action in checked[index] if action.valid]
            checked[index] = _one_per_record(fresh + kept, drop_repeats=True)
        emit("writer", status="done", model=team.writer_model, actions=sum(len(plan.actions) for plan in plans.values()))

    stock = _stock_alternatives(findings)
    cards = []
    for index, case in enumerate(cases):
        plan = plans.get(index)
        validated = checked.get(index, [])
        for action in validated:
            emit("validator", action=action.name, ok=action.valid, text="; ".join(action.problems))
        cards.append(Card(
            finding=case.finding,
            confidence=case.confidence,
            analyst_confidence=case.finding.confidence,
            skeptic=case.skeptic,
            alternatives=_merge_alternatives(case.skeptic, case.finding, stock),
            check_steps=tuple(plan.check_steps) if plan else (),
            actions=tuple(action for action in validated if action.valid),
            rejected_actions=tuple(action for action in validated if not action.valid),
            actions_why=plan.why if plan else "",
            owner_said=owner_said.get(case.finding.subject_id, ()),
        ))

    cards.sort(key=lambda card: _ORDER[card.confidence])
    notes = tuple(
        f"{role}: {error}" for role, error in (("skeptic", skeptic.error), ("writer", writer.error)) if error
    )
    usage = (
        Usage("skeptic", team.skeptic_model, skeptic.input_tokens, skeptic.output_tokens),
        Usage("writer", team.writer_model, *writer_tokens),
    )
    return Recommendations(tuple(cards), notes, usage)


async def _validated(plan: CasePlan, case: CaseInput, evidence: Evidence, nazar: NazarClient) -> list[ValidatedAction]:
    return _one_per_record([
        await validate_action(
            proposal.action,
            proposal.raw_params(),
            context=Context(case.confidence, case.skeptic),
            evidence=evidence,
            nazar=nazar,
        )
        for proposal in plan.actions
    ])


SAME_RECORD = "acts on the same record as another action in this case: each person needs their own record's id"


def _one_per_record(actions: list[ValidatedAction], *, drop_repeats: bool = False) -> list[ValidatedAction]:
    """One action of a kind per record in a card. Live, the writer gave four people's reviews one
    person's check-in id; each passed alone, and "Apply" on Grace's would have written into Tom's.

    A repeat goes back to the writer (``fixable``); ``drop_repeats`` — after the second try, the
    same action said twice is left out quietly."""
    seen: set[tuple[str, tuple[str, ...]]] = set()
    kept: list[ValidatedAction] = []
    for action in actions:
        if not action.valid or action.params is None:
            kept.append(action)
            continue
        key = (action.name, tuple(CATALOG[action.name].targets(action.params)))
        if key in seen:
            if not drop_repeats:
                kept.append(replace(action, preview=None, problems=(SAME_RECORD,), fixable=True))
            continue
        seen.add(key)
        kept.append(action)
    return kept


def record_names(evidence: Evidence) -> dict[str, str]:
    """Whose each record is, as the tools named it: the writer picks a person's own record by it."""
    names = {}
    for record_id, record in evidence.records.items():
        name = record.get("employee_name") or record.get("full_name") or record.get("object_name") or record.get("name")
        if isinstance(name, str) and name:
            names[record_id] = name
    return names


def _key(text: str) -> str:
    return text.strip().casefold()


def _stock_alternatives(findings: list[Finding]) -> frozenset[str]:
    """Explanations the analyst repeated word for word in several findings.

    Said about every case, they say nothing about this one.
    """
    counts: dict[str, int] = {}
    for finding in findings:
        for key in {_key(text) for text in finding.alternatives}:
            counts[key] = counts.get(key, 0) + 1
    return frozenset(key for key, count in counts.items() if count > 1)


def _merge_alternatives(
    skeptic: SkepticView | None, finding: Finding, stock: frozenset[str] = frozenset()
) -> tuple[str, ...]:
    """The skeptic's explanations first: it looked for them on purpose.

    A stock explanation stays only when the card would have none without it.
    """
    merged: list[str] = []
    seen: set[str] = set()
    for text in [*(skeptic.innocent_explanations if skeptic else ()), *finding.alternatives]:
        key = _key(text)
        if key and key not in seen and key not in stock:
            seen.add(key)
            merged.append(text.strip())
    if not merged:
        return _merge_alternatives(skeptic, finding)
    return tuple(merged[:MAX_ALTERNATIVES])
