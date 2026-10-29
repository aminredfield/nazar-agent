"""The company as NAZAR keeps it, for the window's Company tab: sites, groups, people, a week, today.

Read-only and straight from NAZAR — the same calls the agents make — so what
"Apply" changed shows up here: a site's shift, a new site or group, people
put on a site, a check-in's review, a day the timesheet counts differently.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from typing import Any

from nazar_agent.nazar import NazarClient
from nazar_agent.tools.common import COMPANY_TZ, call_nazar
from nazar_agent.tools.facts import today_view
from nazar_agent.tools.schedules import scan_events

WEEK_DAYS = 7
MAX_CHECK_INS = 80  # today's latest check-ins shown


async def snapshot(nazar: NazarClient, now: datetime) -> dict[str, Any]:
    today = now.astimezone(COMPANY_TZ).date()
    first = today - timedelta(days=WEEK_DAYS - 1)
    objects, employees, departments, matrix, (events, truncated) = await asyncio.gather(
        call_nazar(nazar.get("/objects", {"include_inactive": False})),
        call_nazar(nazar.get("/employees")),
        call_nazar(nazar.get("/departments")),
        call_nazar(nazar.get("/timesheet", {"date_from": first.isoformat(), "date_to": today.isoformat()})),
        scan_events(nazar, today, today),
    )
    per_site = await asyncio.gather(*(_site_details(nazar, item["id"]) for item in objects))
    days = matrix.get("days", [])
    today_rows = [row for row in days if row.get("work_date") == today.isoformat()]
    present = today_view(now, employees, objects, today_rows, events, truncated)
    return {
        "date": today.isoformat(),
        "now": now.astimezone(COMPANY_TZ).strftime("%H:%M"),
        "sites": [_site(item, *details) for item, details in zip(objects, per_site, strict=True)],
        "groups": _groups(departments, employees),
        "people": _people(employees, objects, per_site, present["people"]),
        "week": _week(employees, days, first, today),
        "check_ins": _check_ins(events),
    }


async def _site_details(nazar: NazarClient, site_id: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    assignments, groups = await asyncio.gather(
        call_nazar(nazar.get(f"/objects/{site_id}/assignments")),
        call_nazar(nazar.get(f"/objects/{site_id}/groups")),
    )
    return assignments.get("assignments", []), groups.get("groups", [])


def _site(item: dict[str, Any], assignments: list[dict[str, Any]], groups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "name": item["name"],
        "address": item.get("address") or "",
        "latitude": item.get("latitude"),
        "longitude": item.get("longitude"),
        "radius_meters": item.get("radius_meters"),
        "shift_start": item.get("shift_start"),
        "shift_end": item.get("shift_end"),
        "working_days": item.get("working_days") or [],
        "late_grace_minutes": item.get("late_grace_minutes"),
        "people": sorted(row["employee_name"] for row in assignments if row.get("is_active", True)),
        "groups": [
            {
                "name": group["name"],
                "shift_start": group.get("shift_start"),
                "shift_end": group.get("shift_end"),
                "working_days": group.get("working_days"),
            }
            for group in groups
        ],
    }


def _groups(departments: list[dict[str, Any]], employees: list[dict[str, Any]]) -> list[dict[str, Any]]:
    members: dict[str, list[str]] = {}
    for person in employees:
        for group in person.get("departments") or []:
            members.setdefault(group["id"], []).append(person["full_name"])
    return [
        {"id": item["id"], "name": item["name"], "color": item.get("color"), "members": sorted(members.get(item["id"], []))}
        for item in departments
    ]


def _people(
    employees: list[dict[str, Any]],
    objects: list[dict[str, Any]],
    per_site: list[tuple[list[dict[str, Any]], list[dict[str, Any]]]],
    present: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    sites_of: dict[str, list[str]] = {}
    for item, (assignments, _) in zip(objects, per_site, strict=True):
        for row in assignments:
            sites_of.setdefault(row["employee_id"], []).append(item["name"])
    today = {row["employee_id"]: row for row in present}
    people = []
    for person in employees:
        if not person.get("is_active", True):
            continue
        now = today.get(person["id"], {})
        people.append({
            "id": person["id"],
            "name": person["full_name"],
            "code": person.get("employee_id"),
            "sites": sites_of.get(person["id"], []),
            "groups": [group["name"] for group in person.get("departments") or []],
            "status": now.get("status"),
            "first_check_in": now.get("first_check_in"),
            "late_minutes": now.get("late_minutes"),
        })
    return sorted(people, key=lambda person: person["name"])


def _week(employees: list[dict[str, Any]], days: list[dict[str, Any]], first: date, last: date) -> dict[str, Any]:
    dates = [(first + timedelta(days=n)).isoformat() for n in range((last - first).days + 1)]
    cells = {(row["employee_id"], row["work_date"]): row for row in days}
    rows = []
    for person in sorted(employees, key=lambda item: item["full_name"]):
        if not person.get("is_active", True):
            continue
        week = [_cell(cells.get((person["id"], day))) for day in dates]
        rows.append({
            "id": person["id"],
            "name": person["full_name"],
            "days": week,
            "worked_minutes": sum(cell["worked_minutes"] for cell in week),
            "late_days": sum(1 for cell in week if cell["late_minutes"]),
        })
    return {"dates": dates, "rows": rows}


def _cell(row: dict[str, Any] | None) -> dict[str, Any]:
    row = row or {}
    return {
        "code": row.get("day_code") or "none",
        "worked_minutes": int(row.get("worked_minutes") or 0),
        "late_minutes": int(row.get("late_minutes") or 0),
        "adjusted": bool(row.get("is_adjusted")),
        "open": bool(row.get("has_open_session")),
    }


def _check_ins(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest = sorted(events, key=lambda item: item["received_at_server"], reverse=True)[:MAX_CHECK_INS]
    return [
        {
            "id": item["id"],
            "time": datetime.fromisoformat(item["received_at_server"]).astimezone(COMPANY_TZ).strftime("%H:%M"),
            "name": item.get("employee_name"),
            "site": item.get("object_name"),
            "type": item.get("event_type"),
            "flags": item.get("suspicious_flags") or [],
            "review": item.get("review_status"),
        }
        for item in latest
    ]
