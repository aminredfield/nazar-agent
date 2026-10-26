"""Read-only tools over the NAZAR timesheet.

NAZAR calculates the timesheet — worked, late, absent, paid minutes per
employee and day — with deterministic, tested code. These tools never
recalculate it. They group NAZAR's own day rows: totals per employee are sums
of NAZAR's numbers, and unusual days are passed through as NAZAR counted them.

A timesheet day has the id ``<employee_id>:<date>`` so a fact can cite it.
"""

from __future__ import annotations

from collections import Counter
from datetime import date
from typing import Any
from uuid import UUID

from pydantic import Field, model_validator

from nazar_agent.engine import Tool, ToolParams
from nazar_agent.nazar import NazarClient
from nazar_agent.tools.attendance import FLAG_MEANINGS
from nazar_agent.tools.common import call_nazar, local_time

MAX_RANGE_DAYS = 31
MAX_UNUSUAL_DAYS = 150

DAY_CODE_MEANINGS = {
    "work": "worked a planned shift",
    "day_off": "not planned to work",
    "absent": "planned shift, no check-in",
    "pending": "the shift is today or still open; not final yet",
}

# What NAZAR's "Setup check" signs mean (setup_health_service.py, built before
# the hackathon): the schedule probably does not match how people work.
SETUP_FINDING_MEANINGS = {
    "no_days_off": "people work every day with no day off in the schedule — the schedule may be wrong",
    "absences_same_weekday": "absences repeat on the same weekday — that day may really be a day off",
    "mass_lateness": "many people are late by a similar amount — the shift start may be set too early",
    "work_outside_schedule": "people often work when the schedule has no shift",
}


class TimesheetSummaryParams(ToolParams):
    date_from: date = Field(description="First day, YYYY-MM-DD.")
    date_to: date = Field(description=f"Last day, YYYY-MM-DD. At most {MAX_RANGE_DAYS} days after date_from.")
    employee_id: UUID | None = Field(default=None, description="Only this employee; leave out for everyone.")

    @model_validator(mode="after")
    def _check_range(self) -> TimesheetSummaryParams:
        if self.date_to < self.date_from:
            raise ValueError("date_to is before date_from")
        if (self.date_to - self.date_from).days > MAX_RANGE_DAYS:
            raise ValueError(f"the range is longer than {MAX_RANGE_DAYS} days")
        return self


class TimesheetDayParams(ToolParams):
    employee_id: UUID
    work_date: date = Field(description="YYYY-MM-DD.")


class SetupCheckParams(ToolParams):
    pass


def timesheet_tools(nazar: NazarClient) -> tuple[Tool, ...]:
    async def get_timesheet_summary(params: TimesheetSummaryParams) -> dict[str, Any]:
        matrix = await call_nazar(
            nazar.get(
                "/timesheet",
                {
                    "date_from": params.date_from.isoformat(),
                    "date_to": params.date_to.isoformat(),
                    "employee_id": str(params.employee_id) if params.employee_id else None,
                },
            )
        )
        names = await _employee_names(nazar)
        objects = await _object_names(nazar)
        return summarize(matrix, names, objects)

    async def get_timesheet_day(params: TimesheetDayParams) -> dict[str, Any]:
        detail = await call_nazar(
            nazar.get(
                "/timesheet/day/detail",
                {"employee_id": str(params.employee_id), "work_date": params.work_date.isoformat()},
            )
        )
        return day_detail_view(detail)

    async def get_setup_check(params: SetupCheckParams) -> dict[str, Any]:
        check = await call_nazar(nazar.get("/timesheet/setup-check"))
        return setup_check_view(check)

    return (
        Tool(
            name="get_timesheet_summary",
            description=(
                "The timesheet as NAZAR calculated it, for a period: totals per employee "
                "(days by kind, worked and paid minutes, lateness, early leaving) and every "
                "unusual day (absent, late, left early, open shift, adjusted, needs review)."
            ),
            params=TimesheetSummaryParams,
            handler=get_timesheet_summary,
        ),
        Tool(
            name="get_timesheet_day",
            description=(
                "One employee's day in the timesheet: how it was counted, the check-ins behind it, "
                "manual adjustments, an absence or excuse, a dispute, and whether the period is closed."
            ),
            params=TimesheetDayParams,
            handler=get_timesheet_day,
        ),
        Tool(
            name="get_setup_check",
            description=(
                "NAZAR's own check of the last four weeks for signs that the schedule does not "
                "match how people really work. Wrong schedules produce false lateness and absences."
            ),
            params=SetupCheckParams,
            handler=get_setup_check,
        ),
    )


def summarize(
    matrix: dict[str, Any],
    names: dict[str, str],
    objects: dict[str, str],
) -> dict[str, Any]:
    rows = matrix.get("days", [])
    by_employee: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        by_employee.setdefault(row["employee_id"], []).append(row)

    unusual = [_day_view(row, names, objects) for row in rows if _is_unusual(row)]
    unusual.sort(key=lambda day: (day["date"], day["employee_name"] or ""))
    return {
        "period": [matrix.get("date_from"), matrix.get("date_to")],
        "period_closed": matrix.get("is_closed"),
        "day_kinds": DAY_CODE_MEANINGS,
        "employees": [
            _employee_totals(employee_id, days, names)
            for employee_id, days in sorted(by_employee.items(), key=lambda item: names.get(item[0], ""))
        ],
        "unusual_days": unusual[:MAX_UNUSUAL_DAYS],
        "unusual_days_truncated": len(unusual) > MAX_UNUSUAL_DAYS,
    }


def day_detail_view(detail: dict[str, Any]) -> dict[str, Any]:
    day = detail.get("day") or {}
    applied = detail.get("applied") or {}
    return {
        **_day_view(day, {}, {}),
        "period_status": detail.get("period_status"),
        "period_closed": detail.get("is_closed"),
        "events": [
            {
                "id": event.get("id"),
                "type": event.get("event_type"),
                "at": local_time(event.get("received_at_server") or event.get("occurred_at")),
                "object_name": event.get("object_name"),
                "flags": [
                    {"code": code, "meaning": FLAG_MEANINGS.get(code, "no description")}
                    for code in event.get("risk_flags") or []
                ],
            }
            for event in detail.get("events") or []
        ],
        "adjustments": [
            {
                "id": adjustment.get("id"),
                "kind": adjustment.get("kind") or adjustment.get("field"),
                "comment": adjustment.get("comment") or adjustment.get("reason"),
                "created_at": local_time(adjustment.get("created_at")),
            }
            for adjustment in detail.get("adjustments") or []
        ],
        "calendar": applied.get("calendar_title"),
        "absence_type": applied.get("absence_type"),
        "excuse_reason": applied.get("excuse_reason"),
        "has_dispute": detail.get("dispute") is not None,
    }


def setup_check_view(check: dict[str, Any]) -> dict[str, Any]:
    return {
        "period": [check.get("date_from"), check.get("date_to")],
        "findings": [
            {
                "id": f"setup:{item['code']}",
                "code": item["code"],
                "meaning": SETUP_FINDING_MEANINGS.get(item["code"], "no description"),
                "count": item.get("count"),
                "employee_ids": [str(value) for value in item.get("employee_ids") or []],
                "employee_names": item.get("employee_names") or [],
                "details": item.get("details") or {},
            }
            for item in check.get("findings") or []
        ],
    }


def _employee_totals(employee_id: str, days: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any]:
    def total(field: str) -> int:
        return sum(day.get(field) or 0 for day in days)

    return {
        "id": employee_id,
        "name": names.get(employee_id),
        "days_by_kind": dict(Counter(day.get("day_code") for day in days)),
        "worked_minutes": total("worked_minutes"),
        "paid_minutes": total("paid_minutes"),
        "planned_minutes": total("schedule_norm_minutes"),
        "late_days": sum(1 for day in days if (day.get("late_minutes") or 0) > 0),
        "late_minutes": total("late_minutes"),
        "early_leave_minutes": total("early_leave_minutes"),
        "overtime_minutes": total("overtime_minutes"),
        "adjusted_days": sum(1 for day in days if day.get("is_adjusted")),
        "needs_review_days": sum(1 for day in days if day.get("needs_review")),
    }


def _is_unusual(row: dict[str, Any]) -> bool:
    return (
        row.get("day_code") in {"absent", "pending"}
        or (row.get("late_minutes") or 0) > 0
        or (row.get("early_leave_minutes") or 0) > 0
        or bool(row.get("needs_review"))
        or bool(row.get("has_open_session"))
        or bool(row.get("has_schedule_overlap"))
        or bool(row.get("is_adjusted"))
    )


def _day_view(row: dict[str, Any], names: dict[str, str], objects: dict[str, str]) -> dict[str, Any]:
    employee_id = row.get("employee_id")
    return {
        "id": f"{employee_id}:{row.get('work_date')}",
        "employee_id": employee_id,
        "employee_name": names.get(employee_id),
        "date": row.get("work_date"),
        "object_name": objects.get(row.get("object_id")),
        "day_kind": row.get("day_code"),
        "planned_minutes": row.get("schedule_norm_minutes"),
        # What NAZAR actually counted. Without it a model guessed live that a
        # day was paid from the first check-in when it was paid from the second.
        "counted_intervals": [
            {"start": local_time(interval.get("start_at")), "end": local_time(interval.get("end_at"))}
            for shift in row.get("shift_breakdown") or []
            for interval in shift.get("intervals") or []
        ],
        "worked_minutes": row.get("worked_minutes"),
        "late_minutes": row.get("late_minutes"),
        "early_leave_minutes": row.get("early_leave_minutes"),
        "absence_type": row.get("absence_type"),
        "excused": row.get("is_excused"),
        "adjusted": row.get("is_adjusted"),
        "open_shift": row.get("has_open_session"),
        "needs_review": row.get("needs_review"),
        "holiday": row.get("is_calendar_holiday"),
    }


async def _employee_names(nazar: NazarClient) -> dict[str, str]:
    employees = await call_nazar(nazar.get("/employees"))
    return {item["id"]: item["full_name"] for item in employees}


async def _object_names(nazar: NazarClient) -> dict[str, str]:
    objects = await call_nazar(nazar.get("/objects", {"include_inactive": True}))
    return {item["id"]: item["name"] for item in objects}
