"""A simplified timesheet for the demo NAZAR — ours, not NAZAR's calculation.

NAZAR's real timesheet (night rules, holidays, cycles, absences, closing a
period) is closed code and stays out of this repository. This is a plain
version with the same fields, enough for the agent's questions:

- the plan is the site's schedule version of that day (``Site.shift_on``);
- the counted time is the day's check-in → check-out pairs, or the intervals of
  an active manual adjustment (an empty one is a voided day);
- late: minutes after the shift start, counted only past the grace period;
  early leave: minutes before the shift end;
- worked: everything between check-in and check-out; overtime: after the shift
  end, paid up to ``MAX_OVERTIME_MINUTES``; paid: worked minus unpaid overtime;
- a day is ``work``, ``absent`` (planned, nothing counted), ``day_off`` (not
  planned) or ``pending`` (today's shift still running, or a shift left open).

The setup check looks for a site whose crew is late by a similar amount most
days, absences gathering on one weekday, and work on days the schedule has
none — the signs that the schedule, not the people, is wrong.

The same ``day_row`` answers the timesheet, the day detail and the "before →
after" preview of an adjustment, so a preview is what applying will give.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Any

from nazar_agent.demo.world import LATE_GRACE_MINUTES, TZ, Event, Person, World, stable_id

EARLY_LEAVE_GRACE_MINUTES = 0
MAX_OVERTIME_MINUTES = 240
AUTO_CLOSE_AFTER_MINUTES = 360  # an open shift is closed this long after its planned end
SETUP_WINDOW_DAYS = 28
# Our own thresholds for the setup check: loud enough to need a look, never on noise.
LATE_SITE_SHARE = 0.6  # of a site's worked planned days
LATE_SITE_MIN_PEOPLE = 2
LATE_SPREAD_MINUTES = 20  # "a similar amount": the middle half of late minutes fits in this
WEEKDAY_MIN_ABSENCES = 6
WEEKDAY_SHARE = 0.45
OFF_SCHEDULE_MIN_DAYS = 4


@dataclass
class Adjustment:
    """A manual change of a day's counted time; ``intervals == []`` voids the day."""

    id: str
    employee_id: str
    work_date: date
    assignment_id: str
    intervals: list[tuple[datetime, datetime]]
    reason: str
    created_by: str
    created_at: datetime
    is_void: bool = False
    is_active: bool = True


@dataclass
class Book:
    """The demo's writable state over the world: adjustments, newest last."""

    adjustments: list[Adjustment] = field(default_factory=list)

    def active_for(self, employee_id: str, work_date: date) -> Adjustment | None:
        current = [
            item for item in self.adjustments
            if item.is_active and item.employee_id == employee_id and item.work_date == work_date
        ]
        return current[-1] if current else None


def assignment_id(person: Person, site_id: str | None = None) -> str:
    return stable_id("assignment", person.id, site_id or person.site_id)


def local_day(moment: datetime) -> date:
    return moment.astimezone(TZ).date()


def iso(moment: datetime | None) -> str | None:
    return None if moment is None else moment.astimezone(UTC).isoformat()


def _minutes(delta: timedelta) -> int:
    return max(0, int(delta.total_seconds() // 60))


def plan_of(world: World, person: Person, day: date) -> tuple[datetime, datetime] | None:
    if person.site_id is None or day < person.assigned_from or not person.is_active:
        return None
    return world.sites[person.site_id].shift_on(day)


def sessions(world: World, person: Person, day: date) -> list[tuple[Event, Event | None]]:
    """The day's check-ins, each with its check-out if there is one yet."""
    mine = world.events_of(person.id)
    check_outs = {event.check_in_id: event for event in mine if event.event_type == "check_out"}
    return [
        (event, check_outs.get(event.id))
        for event in mine
        if event.event_type == "check_in" and local_day(event.at) == day
    ]


def day_row(
    world: World,
    book: Book,
    person: Person,
    day: date,
    *,
    override: list[tuple[datetime, datetime]] | None = None,
) -> dict[str, Any]:
    """One employee's day as NAZAR's timesheet row; ``override`` previews an adjustment."""
    plan = plan_of(world, person, day)
    adjustment = book.active_for(person.id, day)
    open_session = False
    if override is not None:
        intervals, adjusted = override, True
    elif adjustment is not None:
        intervals, adjusted = adjustment.intervals, True
    else:
        intervals, adjusted = [], False
        for check_in, check_out in sessions(world, person, day):
            if check_out is not None:
                intervals.append((check_in.at, check_out.at))
            elif plan is None or world.now < plan[1] + timedelta(minutes=AUTO_CLOSE_AFTER_MINUTES):
                open_session = True
    intervals = sorted(intervals)

    worked = sum(_minutes(end - start) for start, end in intervals)
    overtime = late = early = 0
    if plan is not None and intervals:
        overtime = sum(_minutes(end - max(start, plan[1])) for start, end in intervals if end > plan[1])
        late_by = _minutes(intervals[0][0] - plan[0])
        late = late_by if late_by > LATE_GRACE_MINUTES else 0
        if not open_session:
            early_by = _minutes(plan[1] - intervals[-1][1])
            early = early_by if early_by > EARLY_LEAVE_GRACE_MINUTES else 0
    unpaid = max(0, overtime - MAX_OVERTIME_MINUTES)
    norm = _minutes(plan[1] - plan[0]) if plan else 0
    still_running = plan is not None and world.now < plan[1]

    if open_session or (still_running and not intervals):
        code = "pending"
    elif plan is None:
        code = "work" if intervals else "day_off"
    else:
        code = "work" if intervals else "absent"
    final = code != "pending"
    paid = worked - unpaid

    site_id = person.site_id if plan is not None or intervals or open_session else None
    shifts = []
    if site_id is not None:
        shifts.append({
            "target_key": f"assignment:{assignment_id(person)}",
            "object_id": site_id,
            "assignment_id": assignment_id(person),
            "is_planned": plan is not None,
            "planned_start_at": iso(plan[0]) if plan else None,
            "planned_end_at": iso(plan[1]) if plan else None,
            "schedule_norm_minutes": norm,
            "intervals": [{"start_at": start.isoformat(), "end_at": end.isoformat()} for start, end in intervals],
            "regular_minutes": worked - overtime,
            "overtime_minutes": overtime - unpaid,
            "holiday_minutes": 0,
            "night_minutes": 0,
            "credited_minutes": 0,
            "credited_pay_minutes": 0,
            "unpaid_minutes": unpaid,
            "overtime_excess_minutes": unpaid,
            "is_adjusted": adjusted,
            "is_counted_shift": bool(intervals),
        })
    return {
        "employee_id": person.id,
        "work_date": day.isoformat(),
        "object_id": site_id,
        "day_code": code,
        "schedule_norm_minutes": norm,
        "required_minutes": norm,
        "worked_minutes": worked,
        "regular_minutes": worked - overtime,
        "credited_minutes": 0,
        "credited_pay_minutes": 0,
        "paid_minutes": paid,
        "overtime_minutes": overtime - unpaid,
        "holiday_minutes": 0,
        "night_minutes": 0,
        "shifts_planned": 1 if plan else 0,
        "shifts_counted": 1 if intervals else 0,
        "shift_breakdown": shifts,
        "late_minutes": late,
        "early_leave_minutes": early,
        "shortfall_minutes": max(0, norm - worked) if plan and final else 0,
        "absence_id": None,
        "absence_type": None,
        "is_excused": False,
        "is_adjusted": adjusted,
        "has_schedule_overlap": False,
        "is_calendar_holiday": False,
        "comment": None,
        "has_open_session": open_session,
        "unpaid_minutes": unpaid,
        "needs_review": unpaid > 0,
    }


def period_rows(
    world: World, book: Book, date_from: date, date_to: date, employee_id: str | None = None
) -> list[dict[str, Any]]:
    days = [date_from + timedelta(days=n) for n in range((date_to - date_from).days + 1)]
    people = [person for person in world.people.values() if employee_id in (None, person.id)]
    return [day_row(world, book, person, day) for person in people for day in days]


def setup_check(world: World, book: Book) -> dict[str, Any]:
    date_to = world.today - timedelta(days=1)
    date_from = date_to - timedelta(days=SETUP_WINDOW_DAYS - 1)
    rows = period_rows(world, book, date_from, date_to)
    names = {person.id: person.name for person in world.people.values()}
    findings = [
        finding
        for finding in (_late_sites(world, rows, names), _absence_weekday(rows, names), _off_schedule(rows, names))
        if finding is not None
    ]
    return {"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "findings": findings}


def _finding(code: str, count: int, employee_ids: list[str], names: dict[str, str], details: dict[str, Any]) -> dict[str, Any]:
    ordered = sorted(set(employee_ids), key=lambda employee: names.get(employee, ""))
    return {
        "code": code,
        "count": count,
        "employee_ids": ordered,
        "employee_names": [names.get(employee, "") for employee in ordered],
        "details": details,
    }


def _late_sites(world: World, rows: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any] | None:
    """Most of a site's crew late by about the same time: the shift start is probably set too early."""
    by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row["day_code"] == "work" and row["shifts_planned"]:
            by_site[row["object_id"]].append(row)
    sites, people, late_days = [], [], 0
    for site_id, worked in by_site.items():
        late = [row for row in worked if row["late_minutes"] > 0]
        late_people = {row["employee_id"] for row in late}
        if len(late) < LATE_SITE_SHARE * len(worked) or len(late_people) < LATE_SITE_MIN_PEOPLE:
            continue
        minutes = sorted(row["late_minutes"] for row in late)
        quarter = len(minutes) // 4
        if minutes[-quarter - 1] - minutes[quarter] > LATE_SPREAD_MINUTES:
            continue  # late by all sorts of times: people, not the schedule
        site = world.sites[site_id]
        sites.append({
            "object_id": site_id,
            "object_name": site.name,
            "shift_start": site.schedule_on(world.today).shift_start,
            "typical_late_minutes": round(median(minutes)),
            "late_days": len(late),
            "worked_days": len(worked),
        })
        people.extend(late_people)
        late_days += len(late)
    if not sites:
        return None
    return _finding("mass_lateness", late_days, people, names, {"objects": sites})


def _absence_weekday(rows: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any] | None:
    """Absences gathering on one weekday: that day may really be a day off."""
    absent = [row for row in rows if row["day_code"] == "absent"]
    if len(absent) < WEEKDAY_MIN_ABSENCES:
        return None
    weekdays = Counter(date.fromisoformat(row["work_date"]).isoweekday() for row in absent)
    weekday, count = weekdays.most_common(1)[0]
    if count / len(absent) < WEEKDAY_SHARE:
        return None
    people = [row["employee_id"] for row in absent if date.fromisoformat(row["work_date"]).isoweekday() == weekday]
    return _finding(
        "absences_same_weekday", count, people, names,
        {"weekday": weekday, "share": round(count / len(absent), 2), "total": len(absent)},
    )


def _off_schedule(rows: list[dict[str, Any]], names: dict[str, str]) -> dict[str, Any] | None:
    """Work on days the schedule has no shift."""
    off = [row for row in rows if row["day_code"] == "work" and not row["shifts_planned"]]
    if len(off) < OFF_SCHEDULE_MIN_DAYS:
        return None
    weekdays = Counter(date.fromisoformat(row["work_date"]).isoweekday() for row in off)
    return _finding(
        "work_outside_schedule", len(off), [row["employee_id"] for row in off], names,
        {"weekdays": dict(sorted(weekdays.items()))},
    )
