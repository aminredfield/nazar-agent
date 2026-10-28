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
from dataclasses import dataclass
from datetime import date

from nazar_agent.findings import Analysis, Evidence, Finding
from nazar_agent.findings.models import Confidence
from nazar_agent.llm import LLMClient
from nazar_agent.nazar import NazarClient
from nazar_agent.recommendations.catalog import Context, ValidatedAction, validate_action
from nazar_agent.recommendations.composer import CaseInput, compose
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
) -> Recommendations:
    """``owner_said`` — the owner's earlier reasons to disagree, by person or site id."""
    findings = analysis.report.findings
    evidence = Evidence.from_steps(analysis.run.steps)
    owner_said = owner_said or {}

    log.info("skeptic: %d finding(s) on %s", len(findings), team.skeptic_model)
    skeptic = await run_skeptic(team.skeptic_llm, team.skeptic_model, analysis.report, evidence, owner_said)
    cases = [
        CaseInput(finding, merge_confidence(finding.confidence, skeptic.views.get(index)), skeptic.views.get(index))
        for index, finding in enumerate(findings)
    ]
    log.info("writer: recommendations on %s", team.writer_model)
    writer = await compose(team.writer_llm, team.writer_model, cases, today)

    stock = _stock_alternatives(findings)
    cards = []
    for index, case in enumerate(cases):
        plan = writer.plans.get(index)
        validated = [
            await validate_action(
                proposal.action,
                proposal.raw_params(),
                context=Context(case.confidence, case.skeptic),
                evidence=evidence,
                nazar=nazar,
            )
            for proposal in (plan.actions if plan else [])
        ]
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
        Usage("writer", team.writer_model, writer.input_tokens, writer.output_tokens),
    )
    return Recommendations(tuple(cards), notes, usage)


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
