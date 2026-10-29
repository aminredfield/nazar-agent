"""JSON views of the agent's results for the assistant window."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from nazar_agent.actions import AuditEntry, Proposal
from nazar_agent.findings import Analysis
from nazar_agent.recommendations import Card, Recommendations
from nazar_agent.recommendations.wording import describe


def answer_view(
    analysis: Analysis,
    recommendations: Recommendations,
    proposals: list[list[Proposal]],
    models: list[dict[str, Any]],
) -> dict[str, Any]:
    """``models`` — every model call of this answer by role (``usage.Meter.by_role``)."""
    return {
        "summary": analysis.report.summary,
        # The direct answer, fact by fact, each verified like a finding's.
        "answer": [
            {"text": fact.text, "evidence": [{"kind": ref.kind, "id": ref.id} for ref in fact.evidence]}
            for fact in analysis.report.answer
        ],
        "cards": [card_view(card, card_proposals) for card, card_proposals in zip(recommendations.cards, proposals, strict=True)],
        "data_gaps": list(analysis.report.data_gaps),
        "notes": list(recommendations.notes),
        "removed_by_fact_checker": [asdict(item) for item in analysis.rejected],
        "fact_checker_rounds": [list(round_) for round_ in analysis.caught],
        "stats": {
            "tool_calls": len(analysis.run.steps),
            "investigation_turns": analysis.run.model_turns,
            "models": models,
            "cost_usd": round(sum(item["cost_usd"] for item in models), 4),
            "cost_estimated": any(item["estimated"] for item in models),
        },
    }


def card_view(card: Card, proposals: list[Proposal]) -> dict[str, Any]:
    finding = card.finding
    return {
        "title": finding.title,
        "subject": {"kind": finding.subject_kind, "id": finding.subject_id, "name": finding.subject_name},
        "confidence": card.confidence,
        "analyst_confidence": card.analyst_confidence,
        "skeptic": None if card.skeptic is None else {"verdict": card.skeptic.verdict, "reasoning": card.skeptic.reasoning},
        "facts": [
            {"text": fact.text, "evidence": [{"kind": ref.kind, "id": ref.id} for ref in fact.evidence]}
            for fact in finding.facts
        ],
        "hypothesis": finding.hypothesis,
        "alternatives": list(card.alternatives),
        "check_steps": list(card.check_steps),
        "actions_why": card.actions_why,
        "owner_said": list(card.owner_said),
        "actions": [proposal_view(proposal) for proposal in proposals],
        "rejected_actions": [{"name": action.name, "problems": list(action.problems)} for action in card.rejected_actions],
    }


def proposal_view(proposal: Proposal) -> dict[str, Any]:
    return {
        "proposal_id": proposal.id,
        "name": proposal.action,
        "level": proposal.level,
        "status": proposal.status,
        "params": proposal.params,
        "preview": proposal.preview,
        # What will change, said by code from the parameters themselves; the
        # reason is what NAZAR's record will say.
        "text": describe(proposal.action, proposal.params, proposal.preview, proposal.card, proposal.card.get("lang", "ru")),
        "reason": proposal.params.get("reason") or proposal.params.get("comment"),
    }


def audit_view(entry: AuditEntry, proposal: Proposal | None) -> dict[str, Any]:
    return {
        "seq": entry.seq,
        "at": entry.at,
        "event": entry.event,
        "actor": entry.actor,
        "details": entry.details,
        "proposal_id": entry.proposal_id,
        "action": proposal.action if proposal else None,
        "card_title": proposal.card.get("title") if proposal else None,
        "subject": proposal.card.get("subject", {}).get("name") if proposal else None,
    }
