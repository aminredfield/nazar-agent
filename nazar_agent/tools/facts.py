"""Facts counted by code, for both analysts: today from the check-ins, a period's tallies, names.

Asked "who was late today", a model read NAZAR's timesheet, where a shift
still open has no lateness yet, and answered "nobody" while a crew had come
an hour late. Asked "how many late days in October", it added numbers up and
got them wrong. These tools count instead, and every row keeps an id a fact
can cite: ``today:<employee_id>``, ``tally:…``, or a timesheet day
``<employee_id>:<date>`` as the timesheet tools name it.
"""

from __future__ import annotations

import os
import re
from collections import defaultdict
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any
from uuid import UUID

from pydantic import Field

from nazar_agent.engine import Tool, ToolParams
from nazar_agent.latin import to_latin
from nazar_agent.nazar import NazarClient
from nazar_agent.tools.common import COMPANY_TZ, call_nazar
from nazar_agent.tools.schedules import scan_events
from nazar_agent.tools.timesheet import PeriodParams

MAX_PEOPLE = 300
MAX_NAMED_DAYS = 400  # people named in a period's days, all days together
MAX_MATCHES = 5
MIN_MATCH = 0.75  # how close a name must be to count as the one asked about
MIN_STEM = 4  # letters two forms of one word share at least
MAX_ENDING = 3  # letters a case ending adds: -ым, -ga, -ning
WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")

LATENESS_RULE = (
    "Late means the first check-in came more than the site's late_grace_minutes after the planned shift start; "
    "within the grace it is not lateness."
)
STATUSES = {
    "not_arrived": "planned today, the shift and its grace have started, no check-in yet",
    "on_site": "checked in, not checked out",
    "left": "checked in and out today",
    "not_started_yet": "planned today, the shift has not started yet",
    "day_off": "no shift planned today",
}
_ORDER = {"not_arrived": 0, "on_site": 1, "left": 2, "not_started_yet": 3, "day_off": 4}



class TodayParams(ToolParams):
    pass


class TallyParams(PeriodParams):
    object_id: UUID | None = Field(default=None, description="Only this site; leave out for the whole company.")


class FindParams(ToolParams):
    query: str = Field(description="A name as the owner wrote it: a person or a site, any letters, typos allowed.")


def fact_tools(nazar: NazarClient, now: datetime) -> tuple[Tool, ...]:
    """``now`` — the company's current moment (the demo's stopped clock in demo mode)."""

    async def get_today(params: TodayParams) -> dict[str, Any]:
        today = now.astimezone(COMPANY_TZ).date()
        events, truncated = await scan_events(nazar, today, today)
        matrix = await call_nazar(nazar.get("/timesheet", {"date_from": today.isoformat(), "date_to": today.isoformat()}))
        employees = await call_nazar(nazar.get("/employees"))
        objects = await call_nazar(nazar.get("/objects", {"include_inactive": True}))
        return today_view(now, employees, objects, matrix.get("days", []), events, truncated)

    async def count_period(params: TallyParams) -> dict[str, Any]:
        query = {"date_from": params.date_from.isoformat(), "date_to": params.date_to.isoformat()}
        matrix = await call_nazar(nazar.get("/timesheet", query))
        employees = await call_nazar(nazar.get("/employees"))
        objects = await call_nazar(nazar.get("/objects", {"include_inactive": True}))
        site = str(params.object_id) if params.object_id else None
        return tally(matrix.get("days", []), employees, objects, params.date_from, params.date_to, now, site)

    async def find_people_and_sites(params: FindParams) -> dict[str, Any]:
        employees = await call_nazar(nazar.get("/employees"))
        objects = await call_nazar(nazar.get("/objects", {"include_inactive": True}))
        return find(params.query, employees, objects)

    return (
        Tool(
            name="get_today",
            description=(
                "Today counted by code from today's check-ins: for every employee whether they are on site, "
                "have left, have not arrived or have no shift, the planned start, the first check-in and how "
                "late it was. Use it for anything about today or now: the timesheet has no lateness for a "
                "shift that is still open."
            ),
            params=TodayParams,
            handler=get_today,
        ),
        Tool(
            name="count_period",
            description=(
                "A period's totals counted by code over NAZAR's timesheet: for the company, per site and per "
                "day — worked minutes, late days and minutes, absences, overtime — with the names of who was "
                "late or absent on each day. Use its totals for any count or sum; never add numbers up yourself."
            ),
            params=TallyParams,
            handler=count_period,
        ),
        Tool(
            name="find_people_and_sites",
            description=(
                "Find employees and sites by a name as the owner wrote it — part of a name, Latin or Cyrillic "
                "letters, a typo. Returns the closest matches with their ids. Two close matches: say both and "
                "ask which one is meant."
            ),
            params=FindParams,
            handler=find_people_and_sites,
        ),
    )


# Today ---------------------------------------------------------------------------------


def today_view(
    now: datetime,
    employees: list[dict[str, Any]],
    objects: list[dict[str, Any]],
    rows: list[dict[str, Any]],
    events: list[dict[str, Any]],
    truncated: bool,
) -> dict[str, Any]:
    now = now.astimezone(COMPANY_TZ)
    sites = {item["id"]: item for item in objects}
    rows_by_person = {row["employee_id"]: row for row in rows}
    events_by_person: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for item in sorted(events, key=_moment):
        if item.get("employee_id"):
            events_by_person[item["employee_id"]].append(item)

    people = [
        _today_person(person, rows_by_person.get(person["id"]), events_by_person.get(person["id"], []), sites, now)
        for person in employees
        if person.get("is_active", True) or person["id"] in events_by_person
    ]
    people.sort(key=lambda person: (_ORDER[person["status"]], -(person["late_minutes"] or 0), person["name"]))
    counts = {status: sum(1 for person in people if person["status"] == status) for status in STATUSES}
    return {
        "now": now.isoformat(timespec="minutes"),
        "date": now.date().isoformat(),
        "rule": LATENESS_RULE,
        "statuses": STATUSES,
        "counts": {
            "id": "today:counts",
            "employees": len(people),
            "planned_today": sum(1 for person in people if person["shift_start"] is not None),
            **counts,
            "late": sum(1 for person in people if (person["late_minutes"] or 0) > 0),
        },
        "people": people[:MAX_PEOPLE],
        "people_truncated": len(people) > MAX_PEOPLE,
        "events_truncated": truncated,
    }


def _today_person(
    person: dict[str, Any], row: dict[str, Any] | None, mine: list[dict[str, Any]], sites: dict[str, Any], now: datetime
) -> dict[str, Any]:
    check_ins = [item for item in mine if item.get("event_type") == "check_in"]
    check_outs = [item for item in mine if item.get("event_type") == "check_out"]
    first = check_ins[0] if check_ins else None
    snapshot = (first or {}).get("schedule_snapshot") or {}
    start = _planned_start(row) or (_parse(snapshot.get("planned_start_at")) if snapshot.get("is_scheduled") else None)
    site_id = (mine[-1].get("object_id") if mine else None) or (row or {}).get("object_id")
    grace = snapshot.get("late_grace_minutes")
    if grace is None:
        grace = (sites.get(site_id) or {}).get("late_grace_minutes") or 0
    grace = int(grace)

    late = None
    if first is not None and start is not None:
        late_by = _moment(first) - start
        late = int(late_by.total_seconds() // 60) if late_by > timedelta(minutes=grace) else 0
    if check_ins:
        status = "on_site" if mine[-1].get("event_type") == "check_in" else "left"
    elif start is None:
        status = "day_off"
    elif now < start + timedelta(minutes=grace):
        status = "not_started_yet"
    else:
        status = "not_arrived"
    return {
        "id": f"today:{person['id']}",
        "employee_id": person["id"],
        "name": person.get("full_name"),
        "site": (sites.get(site_id) or {}).get("name"),
        "status": status,
        "shift_start": _clock(start),
        "late_grace_minutes": grace if start is not None else None,
        "first_check_in": _clock(_moment(first)) if first else None,
        "late_minutes": late,
        "last_check_out": _clock(_moment(check_outs[-1])) if check_outs else None,
    }


def _planned_start(row: dict[str, Any] | None) -> datetime | None:
    for shift in (row or {}).get("shift_breakdown") or []:
        if shift.get("is_planned") is not False and shift.get("planned_start_at"):
            return _parse(shift["planned_start_at"])
    return None


# A period ------------------------------------------------------------------------------


def tally(
    rows: list[dict[str, Any]],
    employees: list[dict[str, Any]],
    objects: list[dict[str, Any]],
    date_from: date,
    date_to: date,
    now: datetime,
    site: str | None = None,
) -> dict[str, Any]:
    names = {item["id"]: item.get("full_name") for item in employees}
    sites = {item["id"]: item for item in objects}
    rows = [row for row in rows if site is None or row.get("object_id") == site]
    scope = f"tally:{site or 'all'}"
    today = now.astimezone(COMPANY_TZ).date()

    by_site: dict[str, list[dict[str, Any]]] = defaultdict(list)
    by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        by_site[row.get("object_id") or ""].append(row)
        by_day[row["work_date"]].append(row)

    named = 0
    days = []
    for work_date in sorted(by_day):
        day_rows = by_day[work_date]
        late = sorted((row for row in day_rows if (row.get("late_minutes") or 0) > 0), key=lambda row: -row["late_minutes"])
        absent = [row for row in day_rows if row.get("day_code") == "absent"]
        shown_late = late[: max(MAX_NAMED_DAYS - named, 0)]
        named += len(shown_late)
        shown_absent = absent[: max(MAX_NAMED_DAYS - named, 0)]
        named += len(shown_absent)
        days.append({
            "id": f"{scope}:{work_date}",
            "date": work_date,
            "weekday": WEEKDAYS[date.fromisoformat(work_date).weekday()],
            **_totals(day_rows),
            "late": [_named(row, names, sites) for row in shown_late],
            "absent": [_named(row, names, sites) for row in shown_absent],
        })

    view: dict[str, Any] = {
        "period": [date_from.isoformat(), date_to.isoformat()],
        "rule": LATENESS_RULE,
        "totals": {"id": f"{scope}:{date_from.isoformat()}:{date_to.isoformat()}", **_totals(rows)},
        "by_site": [
            {
                "id": f"tally:{object_id}:{date_from.isoformat()}:{date_to.isoformat()}",
                "object_id": object_id,
                "name": (sites.get(object_id) or {}).get("name"),
                "late_grace_minutes": (sites.get(object_id) or {}).get("late_grace_minutes"),
                **_totals(site_rows),
            }
            for object_id, site_rows in sorted(by_site.items(), key=lambda item: (sites.get(item[0]) or {}).get("name") or "")
            if object_id
        ],
        "by_day": days,
        "names_truncated": named >= MAX_NAMED_DAYS,
    }
    if date_to >= today:
        view["today_note"] = (
            "Today's shifts that are still open have no lateness in the timesheet yet: "
            "get_today counts today from the check-ins."
        )
    return view


def _totals(rows: list[dict[str, Any]]) -> dict[str, Any]:
    def total(field: str) -> int:
        return sum(row.get(field) or 0 for row in rows)

    return {
        "people": len({row["employee_id"] for row in rows if row.get("day_code") != "day_off" or row.get("worked_minutes")}),
        "worked_minutes": total("worked_minutes"),
        "paid_minutes": total("paid_minutes"),
        # Hours to say to a person: "2,836 hours" reads, "170,203 minutes" does not.
        "worked_hours": round(total("worked_minutes") / 60, 1),
        "paid_hours": round(total("paid_minutes") / 60, 1),
        "late_days": sum(1 for row in rows if (row.get("late_minutes") or 0) > 0),
        "late_minutes": total("late_minutes"),
        "absent_days": sum(1 for row in rows if row.get("day_code") == "absent"),
        "overtime_minutes": total("overtime_minutes"),
        "days_not_final": sum(1 for row in rows if row.get("day_code") == "pending"),
    }


def _named(row: dict[str, Any], names: dict[str, Any], sites: dict[str, Any]) -> dict[str, Any]:
    # The timesheet tools' id for the same day: a fact may cite either.
    return {
        "id": f"{row['employee_id']}:{row['work_date']}",
        "employee_id": row["employee_id"],
        "employee_name": names.get(row["employee_id"]),
        "object_name": (sites.get(row.get("object_id")) or {}).get("name"),
        "day_kind": row.get("day_code"),
        "late_minutes": row.get("late_minutes"),
    }


# Names ---------------------------------------------------------------------------------


def find(query: str, employees: list[dict[str, Any]], objects: list[dict[str, Any]]) -> dict[str, Any]:
    candidates = [
        ("employee", item["id"], item.get("full_name") or "", item.get("is_active", True)) for item in employees
    ] + [("object", item["id"], item.get("name") or "", item.get("is_active", True)) for item in objects]
    scored = sorted(
        ((similarity(query, name), kind, id_, name, active) for kind, id_, name, active in candidates),
        key=lambda item: -item[0],
    )
    matches = [
        {"kind": kind, "id": id_, "name": name, "active": active, "match": round(score, 2)}
        for score, kind, id_, name, active in scored[:MAX_MATCHES]
        if score >= MIN_MATCH
    ]
    close = [match for match in matches if matches[0]["match"] - match["match"] < 0.05]
    return {
        "query": query,
        "matches": matches,
        "ambiguous": len(close) > 1,
        "note": "No one by this name." if not matches else "Several close matches: name them and ask which one." if len(close) > 1 else "",
    }


def similarity(query: str, name: str) -> float:
    """How well a name answers a query: each word of the query against its best word of the name."""
    asked, words = _words(query), _words(name)
    if not asked or not words:
        return 0.0
    return sum(max(_word_match(word, other) for other in words) for word in asked) / len(asked)


def _word_match(asked: str, word: str) -> float:
    if word.startswith(asked) and len(asked) >= 3:
        return 1.0  # "Rustam" in "Rustam", "Karimov" for "Karimova"
    common = len(os.path.commonprefix([asked, word]))
    if common >= MIN_STEM and common >= min(len(asked), len(word)) - 1 and abs(len(asked) - len(word)) <= MAX_ENDING:
        return 1.0  # the same word with a case ending: "Karimovim", "Rustamga" for "Karimova", "Rustam"
    shorter = word[: len(asked) + 1] if len(word) > len(asked) else word
    return max(SequenceMatcher(None, asked, word).ratio(), SequenceMatcher(None, asked, shorter).ratio())


def _words(text: str) -> list[str]:
    plain = to_latin(text)
    # Uzbek forms of address and Russian case endings say nothing about who it is.
    return [word for word in re.findall(r"[a-z0-9]+", plain) if word not in {"aka", "opa"} and len(word) > 1]


# Time ----------------------------------------------------------------------------------


def _moment(item: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(item["received_at_server"]).astimezone(COMPANY_TZ)


def _parse(value: Any) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value).astimezone(COMPANY_TZ)
    except ValueError:
        return None


def _clock(moment: datetime | None) -> str | None:
    return moment.astimezone(COMPANY_TZ).strftime("%H:%M") if moment else None
