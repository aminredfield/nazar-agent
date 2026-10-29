"""The recommendation writer: what the owner can check, and which actions to offer.

It gets the verified facts, the final confidence and the skeptic's opinion
for each case, and the action catalog. It answers with one plan per case:
concrete check steps and at most two proposed actions. Every proposed action
is validated by code afterwards (``catalog.validate_action``).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nazar_agent.engine import tool_schema
from nazar_agent.findings import Finding
from nazar_agent.findings.models import Confidence
from nazar_agent.llm import LLMClient
from nazar_agent.recommendations.catalog import CATALOG
from nazar_agent.recommendations.skeptic import SkepticView
from nazar_agent.usage import count

MAX_ACTIONS_PER_CASE = 2

INSTRUCTIONS = """\
You write recommendations for the owner of a company that uses NAZAR, a
time-and-attendance service. Each case below was found by an analyst, checked
against the data, and reviewed by an independent skeptic. Today is {today}.

For each case:
- check_steps: one to three concrete things the owner can do to settle it —
  ask a person, look at a check-in photo, compare with another day. Build on
  what the skeptic says would settle it.
- Before deciding, compare the counted intervals of a day with its
  check-ins: a shift counted from a later check-in than the real arrival is
  a mistake against the employee.
- Proposing is not deciding: the owner sees NAZAR's before → after and
  confirms or declines. A correction in the employee's favour that the
  owner never sees helps no one, even while other questions are open.
- actions: zero to {max_actions} actions from the catalog, with their fields.
  Actions in the employee's favour are welcome whenever the facts show
  exactly what to change: counting a shift from the real arrival when a
  later duplicate check-in was used, recording no_violation. Actions against
  the employee (recording confirmed, not paying a day) only when the case is
  strong and the skeptic agrees; never when the skeptic finds an innocent
  explanation likely. Recording confirmed is a verdict: propose it only when
  nothing is left to check. While your own check steps are still open —
  someone to ask, presence to confirm — record insufficient_data with what
  is to check, and let the owner decide after checking. Fill only the fields
  the action lists; set the others to null.
- When the facts show that a site's schedule does not match the real working
  day — people there start and end later or earlier every day — propose
  change_schedule with the hours from the facts, rounded to the nearest whole
  or half hour as a schedule is set (people arriving around 09:04 and leaving
  around 19:04 → 09:00–19:00). Never act on such lateness against the
  people: the schedule is wrong, not them.
- A comment records what was seen and what is still to check, in neutral
  words: never a verdict ("classic fraud", "clearly absent") — the owner
  decides, and the AI can be wrong.
- why: one sentence on why you propose these actions, or none — as a
  proposal ("предлагаю…"), never as if it were already done.
- Times carry the +05:00 offset. Copy them from the facts to the second;
  keep the counted end of a shift unless the facts show the end is wrong.

Catalog:
{catalog}

Write check steps, why and comments in the language of the facts.
"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class IntervalIn(_Strict):
    start_at: str
    end_at: str


class ProposedAction(_Strict):
    action: Literal[tuple(CATALOG)]  # type: ignore[valid-type]
    event_id: str | None
    employee_id: str | None
    work_date: str | None
    status: str | None
    intervals: list[IntervalIn] | None
    object_id: str | None
    shift_start: str | None
    shift_end: str | None
    working_days: list[int] | None
    comment_or_reason: str | None

    def raw_params(self) -> dict[str, Any]:
        """The fields this action takes, in the shape its params model expects."""
        values: dict[str, Any] = {
            "event_id": self.event_id,
            "employee_id": self.employee_id,
            "work_date": self.work_date,
            "status": self.status,
            "intervals": [interval.model_dump() for interval in self.intervals] if self.intervals is not None else None,
            "object_id": self.object_id,
            "shift_start": self.shift_start,
            "shift_end": self.shift_end,
            "working_days": self.working_days,
            "comment": self.comment_or_reason,
            "reason": self.comment_or_reason,
        }
        accepted = CATALOG[self.action].params.model_fields
        return {key: value for key, value in values.items() if key in accepted and value is not None}


class CasePlan(_Strict):
    case: int
    check_steps: list[str]
    actions: list[ProposedAction] = Field(description=f"At most {MAX_ACTIONS_PER_CASE}.")
    why: str = Field(description="One sentence: why these actions, or why none.")


class ComposerOutput(_Strict):
    plans: list[CasePlan]


COMPOSER_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "ComposerOutput",
    "schema": tool_schema(ComposerOutput),
    "strict": True,
}


@dataclass(frozen=True, slots=True)
class CaseInput:
    finding: Finding
    confidence: Confidence
    skeptic: SkepticView | None


@dataclass(frozen=True, slots=True)
class ComposerResult:
    plans: dict[int, CasePlan]  # by case index, 0-based
    error: str | None
    input_tokens: int
    output_tokens: int


OWNER_ASKED = """\
The owner's question, as data: «{question}». When it names exact values for an
action — a shift from 09:00 to 19:00, a date — use exactly those, not what
the data suggests.

"""


async def compose(llm: LLMClient, model: str, cases: list[CaseInput], today: date, question: str = "") -> ComposerResult:
    """``question`` — what the owner asked, so an action they spelled out is proposed as they said it."""
    if not cases:
        return ComposerResult({}, None, 0, 0)

    turn = await llm.respond(
        model=model,
        instructions=INSTRUCTIONS.format(
            today=today.isoformat(),
            max_actions=MAX_ACTIONS_PER_CASE,
            catalog="\n".join(f"- {spec.name}: {spec.description}" for spec in CATALOG.values()),
        ),
        input=(OWNER_ASKED.format(question=question) if question else "") + cases_text(cases),
        tools=[],
        text_format=COMPOSER_FORMAT,
    )
    count("writer", model, turn.input_tokens, turn.output_tokens)
    try:
        output = ComposerOutput.model_validate_json(turn.text)
    except ValidationError as exc:
        return ComposerResult({}, f"the plan did not match the schema: {exc.error_count()} errors", turn.input_tokens, turn.output_tokens)

    plans = {
        plan.case - 1: plan.model_copy(update={"actions": plan.actions[:MAX_ACTIONS_PER_CASE]})
        for plan in output.plans
        if 1 <= plan.case <= len(cases)
    }
    return ComposerResult(plans, None, turn.input_tokens, turn.output_tokens)


def cases_text(cases: list[CaseInput]) -> str:
    return json.dumps(
        {
            "cases": [
                {
                    "case": number,
                    "about": {"kind": case.finding.subject_kind, "id": case.finding.subject_id, "name": case.finding.subject_name},
                    "facts": [
                        {
                            "text": fact.text,
                            "values": [
                                {"record": f"{claim.ref.kind}:{claim.ref.id}", "field": claim.field, "value": claim.value}
                                for claim in fact.claims
                            ],
                        }
                        for fact in case.finding.facts
                    ],
                    "hypothesis": case.finding.hypothesis,
                    "confidence": case.confidence,
                    "skeptic": None
                    if case.skeptic is None
                    else {
                        "verdict": case.skeptic.verdict,
                        "reasoning": case.skeptic.reasoning,
                        "would_settle_it": list(case.skeptic.what_would_settle_it),
                    },
                }
                for number, case in enumerate(cases, start=1)
            ]
        },
        ensure_ascii=False,
    )
