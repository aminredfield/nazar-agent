"""The fixed catalog of actions an agent may propose, and their validation.

A model can only pick an action from this list and fill its parameters. Code
then decides whether the proposal stands:

1. parameters parse into the action's typed model;
2. every record it touches was actually returned by a tool in this run —
   an agent cannot act on an id it has not seen;
3. policy, in code: e.g. recording "confirmed" or not paying a day needs high
   confidence and a skeptic who agrees;
4. for anything that changes pay, NAZAR itself computes "before → after"
   (``/timesheet/adjustments/preview``, nothing is saved).

Nothing here writes to NAZAR. Execution after a human confirms is separate.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from nazar_agent.engine import ToolExecutionError
from nazar_agent.findings import Evidence
from nazar_agent.findings.models import Confidence
from nazar_agent.nazar import NazarClient
from nazar_agent.recommendations.skeptic import SkepticView
from nazar_agent.tools.common import call_nazar

ActionLevel = Literal["record_only", "changes_pay"]
ReviewStatus = Literal["no_violation", "confirmed", "insufficient_data"]
DAY_FIELDS = ("day_code", "worked_minutes", "paid_minutes", "late_minutes", "early_leave_minutes")
MAX_INTERVALS = 4


class _Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReviewEventParams(_Params):
    event_id: UUID
    status: ReviewStatus
    comment: str = Field(min_length=1, max_length=500)


class Interval(_Params):
    start_at: datetime
    end_at: datetime

    @model_validator(mode="after")
    def _ordered_and_aware(self) -> Interval:
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("times need a UTC offset, e.g. 2026-10-23T09:06:00+05:00")
        if self.end_at <= self.start_at:
            raise ValueError("end_at is not after start_at")
        return self


class AdjustShiftParams(_Params):
    employee_id: UUID
    work_date: date
    intervals: list[Interval] = Field(min_length=1, max_length=MAX_INTERVALS)
    reason: str = Field(min_length=1, max_length=500)

    @model_validator(mode="after")
    def _on_that_day(self) -> AdjustShiftParams:
        # A night shift may end the next morning; nothing else leaves the day.
        latest = self.work_date + timedelta(days=1)
        for interval in self.intervals:
            if not self.work_date <= interval.start_at.date() <= latest:
                raise ValueError(f"interval starts outside {self.work_date}")
        return self


class VoidShiftParams(_Params):
    employee_id: UUID
    work_date: date
    reason: str = Field(min_length=1, max_length=500)


@dataclass(frozen=True, slots=True)
class Preview:
    before: dict[str, Any] | None
    after: dict[str, Any] | None


@dataclass(frozen=True, slots=True)
class Context:
    """What the action is based on: the card's final confidence and the skeptic."""

    confidence: Confidence
    skeptic: SkepticView | None


@dataclass(frozen=True, slots=True)
class ActionSpec:
    name: str
    level: ActionLevel
    description: str
    params: type[_Params]
    targets: Callable[[Any], list[str]]
    policy: Callable[[Any, Context], str | None]
    preview: Callable[[NazarClient, Any], Awaitable[Preview]] | None


@dataclass(frozen=True, slots=True)
class ValidatedAction:
    name: str
    level: ActionLevel
    params: _Params | None
    preview: Preview | None
    problems: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.problems


def _strong_case(context: Context) -> bool:
    return context.confidence == "high" and context.skeptic is not None and context.skeptic.verdict == "concern_supported"


def _review_policy(params: ReviewEventParams, context: Context) -> str | None:
    if params.status == "confirmed" and not _strong_case(context):
        return "recording a confirmed violation needs high confidence and a skeptic who agrees"
    return None


def _void_policy(params: VoidShiftParams, context: Context) -> str | None:
    if not _strong_case(context):
        return "not paying a day needs high confidence and a skeptic who agrees"
    return None


async def _day_row(nazar: NazarClient, employee_id: UUID, work_date: date) -> dict[str, Any] | None:
    matrix = await call_nazar(
        nazar.get(
            "/timesheet",
            {"date_from": work_date.isoformat(), "date_to": work_date.isoformat(), "employee_id": str(employee_id)},
        )
    )
    rows = matrix.get("days", [])
    return _day_fields(rows[0]) if rows else None


async def _preview_intervals(
    nazar: NazarClient, employee_id: UUID, work_date: date, intervals: list[Interval]
) -> Preview:
    before = await _day_row(nazar, employee_id, work_date)
    rows = await call_nazar(
        nazar.post(
            "/timesheet/adjustments/preview",
            {
                "items": [{
                    "employee_id": str(employee_id),
                    "work_date": work_date.isoformat(),
                    "intervals": [
                        {"start_at": item.start_at.isoformat(), "end_at": item.end_at.isoformat()} for item in intervals
                    ],
                }]
            },
        )
    )
    return Preview(before=before, after=_day_fields(rows[0]) if rows else None)


async def _adjust_preview(nazar: NazarClient, params: AdjustShiftParams) -> Preview:
    return await _preview_intervals(nazar, params.employee_id, params.work_date, params.intervals)


async def _void_preview(nazar: NazarClient, params: VoidShiftParams) -> Preview:
    return await _preview_intervals(nazar, params.employee_id, params.work_date, [])


def _day_fields(row: dict[str, Any]) -> dict[str, Any]:
    return {field: row.get(field) for field in DAY_FIELDS}


CATALOG: dict[str, ActionSpec] = {
    spec.name: spec
    for spec in (
        ActionSpec(
            name="review_event",
            level="record_only",
            description=(
                "Record the review result of a flagged check-in: no_violation, insufficient_data, "
                "or confirmed (only with high confidence). Changes no money. "
                "Fields: event_id, status, comment."
            ),
            params=ReviewEventParams,
            targets=lambda params: [str(params.event_id)],
            policy=_review_policy,
            preview=None,
        ),
        ActionSpec(
            name="adjust_shift",
            level="changes_pay",
            description=(
                "Replace the counted work intervals of one employee's day, e.g. count the shift from "
                "the real arrival when a later duplicate check-in was used. Changes pay; NAZAR shows "
                "before → after. Fields: employee_id, work_date, intervals (with +05:00), reason."
            ),
            params=AdjustShiftParams,
            targets=lambda params: [str(params.employee_id)],
            policy=lambda params, context: None,
            preview=_adjust_preview,
        ),
        ActionSpec(
            name="void_shift",
            level="changes_pay",
            description=(
                "Mark a day as not worked because the check-in was not this employee's. Changes pay; "
                "only with high confidence and a skeptic who agrees. Fields: employee_id, work_date, reason."
            ),
            params=VoidShiftParams,
            targets=lambda params: [str(params.employee_id)],
            policy=_void_policy,
            preview=_void_preview,
        ),
    )
}


async def validate_action(
    name: str,
    raw_params: dict[str, Any],
    *,
    context: Context,
    evidence: Evidence,
    nazar: NazarClient,
) -> ValidatedAction:
    spec = CATALOG.get(name)
    if spec is None:
        return ValidatedAction(name, "record_only", None, None, (f"unknown action {name!r}",))

    def rejected(*problems: str, params: _Params | None = None) -> ValidatedAction:
        return ValidatedAction(spec.name, spec.level, params, None, problems)

    try:
        params = spec.params.model_validate(raw_params)
    except ValidationError as exc:
        return rejected(*(f"{'.'.join(map(str, error['loc']))}: {error['msg']}" for error in exc.errors()))

    unseen = [target for target in spec.targets(params) if target not in evidence.seen_ids]
    if unseen:
        return rejected(f"acts on records no tool returned: {', '.join(unseen)}", params=params)

    violation = spec.policy(params, context)
    if violation:
        return rejected(violation, params=params)

    if spec.preview is None:
        return ValidatedAction(spec.name, spec.level, params, None, ())
    try:
        preview = await spec.preview(nazar, params)
    except ToolExecutionError as exc:
        return rejected(f"NAZAR could not compute before → after: {exc}", params=params)
    return ValidatedAction(spec.name, spec.level, params, preview, ())
