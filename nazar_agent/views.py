"""JSON views of the agent's results for the assistant window."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from nazar_agent.actions import AuditEntry, Proposal
from nazar_agent.findings import Analysis
from nazar_agent.llm.pricing import cost_usd
from nazar_agent.recommendations import Card, Recommendations, Usage


def answer_view(
    analysis: Analysis,
    recommendations: Recommendations,
    proposals: list[list[Proposal]],
    analyst_model: str,
    photos: Usage | None = None,
) -> dict[str, Any]:
    usage = [("analyst", analyst_model, analysis.input_tokens, analysis.output_tokens)] + [
        (item.role, item.model, item.input_tokens, item.output_tokens)
        for item in ((photos,) if photos else ()) + tuple(recommendations.usage)
    ]
    costs = [cost_usd(model, tokens_in, tokens_out) for _, model, tokens_in, tokens_out in usage]
    return {
        "summary": analysis.report.summary,
        "cards": [card_view(card, card_proposals) for card, card_proposals in zip(recommendations.cards, proposals, strict=True)],
        "data_gaps": list(analysis.report.data_gaps),
        "notes": list(recommendations.notes),
        "removed_by_fact_checker": [asdict(item) for item in analysis.rejected],
        "fact_checker_rounds": [list(round_) for round_ in analysis.caught],
        "stats": {
            "tool_calls": len(analysis.run.steps),
            "investigation_turns": analysis.run.model_turns,
            "models": [
                {"role": role, "model": model, "input_tokens": tokens_in, "output_tokens": tokens_out, "cost_usd": cost}
                for (role, model, tokens_in, tokens_out), cost in zip(usage, costs, strict=True)
            ],
            "cost_usd": round(sum(cost for cost in costs if cost is not None), 4),
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
