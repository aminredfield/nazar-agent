"""The executor: the only part of the system that writes to NAZAR.

It is plain code and acts only on a human's decision:

- ``propose`` stores the validated actions of a set of cards as pending;
- ``confirm`` claims a pending proposal atomically, checks policy again,
  asks NAZAR for the day once more and refuses if it changed since the human
  saw "before → after", then executes and records the result;
- ``decline`` records the human's reason — it is feedback, not just a no;
- ``undo`` puts NAZAR back the way the action's own undo knows how.

Every outcome, including refusals and failures, lands in the audit trail.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Literal

from pydantic import BaseModel

from nazar_agent.actions.store import Proposal, ProposalStore
from nazar_agent.engine import ToolExecutionError
from nazar_agent.nazar import NazarClient
from nazar_agent.recommendations import CATALOG, Card, Context, SkepticView

ErrorCode = Literal["not_found", "wrong_status", "refused", "stale", "failed"]
_TEXT_FIELDS = ("reason", "comment")
_MAX_TEXT = 500
AGENT_ACTOR = "nazar-agent"


class ExecutionError(Exception):
    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class Executor:
    store: ProposalStore
    nazar: NazarClient

    def propose(self, cards: tuple[Card, ...], question: str, lang: str = "ru") -> list[list[Proposal]]:
        """Store every valid action as pending; one list of proposals per card, in card order.

        ``lang`` — the question's language, in which the owner is told what each action will do.
        """
        return [
            [
                self.store.add(
                    action=action.name,
                    level=action.level,
                    params=action.params.model_dump(mode="json"),
                    preview=asdict(action.preview) if action.preview else None,
                    context={
                        "confidence": card.confidence,
                        "skeptic_verdict": card.skeptic.verdict if card.skeptic else None,
                    },
                    card={**card_snapshot(card), "about": action.about, "lang": lang},
                    question=question,
                    actor=AGENT_ACTOR,
                )
                for action in card.actions
                if action.params is not None
            ]
            for card in cards
        ]

    async def confirm(self, proposal_id: str, actor: str) -> Proposal:
        proposal = self._get(proposal_id)
        if self.store.transition(proposal_id, expected="pending", to="executing") is None:
            raise ExecutionError("wrong_status", f"The proposal is {proposal.status}, not pending.")

        spec = CATALOG[proposal.action]
        params = spec.params.model_validate(proposal.params)
        violation = spec.policy(params, _context(proposal))
        if violation:
            self.store.transition(proposal_id, expected="executing", to="stale", audit=("refused", actor, {"reason": violation}))
            raise ExecutionError("refused", violation)

        after = None
        if spec.preview is not None:
            try:
                fresh = await spec.preview(self.nazar, params)
            except ToolExecutionError as exc:
                return self._fail(proposal_id, actor, "executing", "pending", str(exc))
            seen_before = (proposal.preview or {}).get("before")
            if fresh.before != seen_before:
                self.store.transition(
                    proposal_id,
                    expected="executing",
                    to="stale",
                    audit=("refused", actor, {"reason": "the day changed since it was proposed", "then": seen_before, "now": fresh.before}),
                )
                raise ExecutionError("stale", "The day changed in NAZAR since this was proposed. Ask again to see it as it is now.")
            after = fresh.after

        try:
            result = await spec.execute(self.nazar, _attributed(params, actor))
        except ToolExecutionError as exc:
            return self._fail(proposal_id, actor, "executing", "failed", str(exc))
        return self._move(proposal_id, "executing", "executed", ("executed", actor, {"result": result, "after": after}), result)

    def decline(self, proposal_id: str, actor: str, reason: str) -> Proposal:
        text = reason.strip()
        if not text:
            raise ExecutionError("refused", "Say why — the next recommendations take it into account.")
        proposal = self.store.transition(
            proposal_id, expected="pending", to="declined", audit=("declined", actor, {"reason": text[:_MAX_TEXT]})
        )
        if proposal is None:
            raise ExecutionError("wrong_status", f"The proposal is {self._get(proposal_id).status}, not pending.")
        return proposal

    async def undo(self, proposal_id: str, actor: str) -> Proposal:
        proposal = self._get(proposal_id)
        if self.store.transition(proposal_id, expected="executed", to="undoing") is None:
            raise ExecutionError("wrong_status", f"Only an executed action can be undone; this one is {proposal.status}.")
        spec = CATALOG[proposal.action]
        try:
            await spec.undo(self.nazar, spec.params.model_validate(proposal.params), proposal.result or {})
        except ToolExecutionError as exc:
            return self._fail(proposal_id, actor, "undoing", "executed", str(exc))
        return self._move(proposal_id, "undoing", "undone", ("undone", actor, {}))

    def _get(self, proposal_id: str) -> Proposal:
        try:
            return self.store.get(proposal_id)
        except KeyError:
            raise ExecutionError("not_found", "No such proposal.") from None

    def _move(self, proposal_id: str, expected: Any, to: Any, audit: Any, result: dict[str, Any] | None = None) -> Proposal:
        proposal = self.store.transition(proposal_id, expected=expected, to=to, result=result, audit=audit)
        if proposal is None:  # only this executor moves a claimed proposal
            raise ExecutionError("wrong_status", "The proposal changed while it was being processed.")
        return proposal

    def _fail(self, proposal_id: str, actor: str, expected: Any, to: Any, error: str) -> Proposal:
        self.store.transition(proposal_id, expected=expected, to=to, audit=("failed", actor, {"error": error}))
        raise ExecutionError("failed", error)


def card_snapshot(card: Card) -> dict[str, Any]:
    """What the human saw when deciding — kept with the proposal and its audit."""
    finding = card.finding
    return {
        "title": finding.title,
        "subject": {"kind": finding.subject_kind, "id": finding.subject_id, "name": finding.subject_name},
        "facts": [fact.text for fact in finding.facts],
        "hypothesis": finding.hypothesis,
        "confidence": card.confidence,
    }


def _context(proposal: Proposal) -> Context:
    verdict = proposal.context.get("skeptic_verdict")
    skeptic = SkepticView(verdict, "", (), ()) if verdict else None
    return Context(proposal.context["confidence"], skeptic)


def _attributed(params: BaseModel, actor: str) -> BaseModel:
    """NAZAR's own records say who decided: the reason names the human."""
    suffix = f" — подтвердил(а): {actor}, через NAZAR Agent"
    updates = {
        field: (getattr(params, field)[: _MAX_TEXT - len(suffix)] + suffix)
        for field in _TEXT_FIELDS
        if hasattr(params, field)
    }
    return params.model_copy(update=updates)
