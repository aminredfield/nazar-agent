"""Each site's schedule next to how people really come and go, counted by code.

A wrong schedule looks like a people problem: a crew that starts at 09:00 on
a site set to 08:00 is "late" every day. NAZAR's setup check says *that* the
schedule may be wrong; this says *where* and *what the day really looks like*:
for every site, the typical first check-in and last check-out over the
period, and how many of NAZAR's late days fall there.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from statistics import median
from typing import Any

from nazar_agent.nazar import NazarClient
from nazar_agent.tools.common import COMPANY_TZ, call_nazar

PAGE_SIZE = 500  # NAZAR's own maximum for /attendance/logs
MAX_SCANNED_EVENTS = 5_000


async def scan_events(
    nazar: NazarClient, date_from: date, date_to: date, *, page_size: int = PAGE_SIZE
) -> tuple[list[dict[str, Any]], bool]:
    """Every check-in and check-out of the local days, page by page; True if cut at the cap."""
    events: list[dict[str, Any]] = []
    while len(events) < MAX_SCANNED_EVENTS:
        page = await call_nazar(
            nazar.get(
                "/attendance/logs",
                {
                    "date_from": local_day_start(date_from).isoformat(),
                    "date_to": local_day_start(date_to + timedelta(days=1)).isoformat(),
                    "limit": page_size,
                    "offset": len(events),
                },
            )
        )
        events.extend(page)
        if len(page) < page_size:
            break
    return events, len(events) >= MAX_SCANNED_EVENTS


def local_day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=COMPANY_TZ)


def site_schedules(
    objects: list[dict[str, Any]], events: list[dict[str, Any]], rows: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Per site: the schedule as set, the real first arrival and last leaving, NAZAR's lateness."""
    firsts: dict[tuple[str, str, date], datetime] = {}
    lasts: dict[tuple[str, str, date], datetime] = {}
    for item in events:
        if not item.get("object_id") or not item.get("employee_id"):
            continue
        moment = datetime.fromisoformat(item["received_at_server"]).astimezone(COMPANY_TZ)
        key = (item["object_id"], item["employee_id"], moment.date())
        if item["event_type"] == "check_in":
            firsts[key] = min(firsts.get(key, moment), moment)
        elif item["event_type"] == "check_out":
            lasts[key] = max(lasts.get(key, moment), moment)

    sites = []
    for site in objects:
        site_id = site["id"]
        arrivals = [moment for key, moment in firsts.items() if key[0] == site_id]
        leavings = [moment for key, moment in lasts.items() if key[0] == site_id]
        late = [row for row in rows if row.get("object_id") == site_id and (row.get("late_minutes") or 0) > 0]
        start, end = site.get("shift_start"), site.get("shift_end")
        sites.append({
            "id": site_id,
            "name": site["name"],
            "schedule_mode": site.get("work_schedule_mode"),
            "shift": [start, end],
            "working_days": site.get("working_days"),
            "people": len({key[1] for key in firsts if key[0] == site_id}),
            "days_with_check_in": len(arrivals),
            "typical_first_check_in": _clock(arrivals),
            "typical_last_check_out": _clock(leavings),
            "days_arrived_after_shift_start": _after(arrivals, start),
            "days_left_after_shift_end": _after(leavings, end),
            "nazar_late_days": len(late),
            "nazar_late_minutes": sum(row["late_minutes"] for row in late),
        })
    return sites


def _minutes(moment: datetime) -> int:
    return moment.hour * 60 + moment.minute


def _clock(moments: list[datetime]) -> str | None:
    if not moments:
        return None
    value = round(median(_minutes(moment) for moment in moments))
    return f"{value // 60:02d}:{value % 60:02d}"


def _after(moments: list[datetime], clock: str | None) -> int | None:
    if clock is None:
        return None
    hours, minutes = map(int, clock.split(":"))
    return sum(1 for moment in moments if _minutes(moment) > hours * 60 + minutes)
