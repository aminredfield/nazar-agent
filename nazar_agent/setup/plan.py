"""The plan of changes for a draft: the steps in the order they will be done, checked by code.

Built from the draft and what NAZAR says now: which sites people already
work on and their shift there (their own, their group's, or the site's), so
a shift that clashes is found before anything is created. A step that needs a
site or a group not made yet names it "$site" / "$group"; applying the plan
fills them in. Nothing here writes to NAZAR.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Any, Literal

from nazar_agent.nazar import NazarClient
from nazar_agent.setup.draft import Draft, PersonCard
from nazar_agent.setup.overlaps import Clash, Option, Shift, clash, options
from nazar_agent.tools.common import COMPANY_TZ, call_nazar

NEW_SITE = "$site"
NEW_GROUP = "$group"
PREVIEW_DAYS = 7
StepKind = Literal[
    "create_site", "site_schedule", "create_group", "group_on_site", "add_to_group", "assign", "transfer", "wait_for_photo"
]
Missing = Literal["site", "site_name", "point", "shift", "choose_person", "nothing"]


@dataclass(frozen=True, slots=True)
class Step:
    kind: StepKind
    params: dict[str, Any]


@dataclass(frozen=True, slots=True)
class PersonClash:
    person: PersonCard
    site_id: str
    site_name: str
    theirs: Shift
    clash: Clash
    options: tuple[Option, ...]


@dataclass(frozen=True, slots=True)
class Plan:
    steps: tuple[Step, ...]
    missing: tuple[tuple[Missing, str], ...]  # what stops the plan, and about whom or what
    clashes: tuple[PersonClash, ...]
    preview: tuple[dict[str, Any], ...]  # the next days of the new shift: date, working, from, to
    site_name: str
    group_name: str
    unknown_schedules: tuple[str, ...] = field(default=())  # people whose other schedule is not weekly: check by hand

    @property
    def ready(self) -> bool:
        return bool(self.steps) and not self.missing


async def build(draft: Draft, nazar: NazarClient, today: date) -> Plan:
    sites, employees = await asyncio.gather(
        call_nazar(nazar.get("/objects", {"include_inactive": False})), call_nazar(nazar.get("/employees"))
    )
    by_site = {item["id"]: item for item in sites}
    site_id = draft.site.existing_id or NEW_SITE
    site_name = by_site[site_id]["name"] if site_id in by_site else draft.site.name
    group_id = draft.group.existing_id or NEW_GROUP
    group_on = draft.group.state == "keep" and bool(draft.group.name)
    people = [card for card in draft.people if card.state != "skip" and not card.choices]

    steps: list[Step] = []
    missing = _missing(draft)
    shift = _new_shift(draft, by_site.get(site_id))
    if draft.site.state == "keep" and not draft.site.existing_id and draft.site.name:
        if draft.site.has_point and draft.shift.is_set:  # otherwise it is asked for (missing)
            steps.append(Step("create_site", _site_params(draft)))
    elif draft.site.existing_id and draft.shift.is_set and draft.shift.state == "keep" and not group_on:
        steps.append(Step("site_schedule", {"object_id": site_id, **_shift_params(draft)}))
    if group_on and not draft.group.existing_id:
        steps.append(Step("create_group", {"name": draft.group.name}))
    if group_on:
        custom = bool(draft.site.existing_id and draft.shift.is_set and draft.shift.state == "keep")
        steps.append(Step("group_on_site", {
            "object_id": site_id, "department_id": group_id, "schedule_mode": "custom" if custom else "object_default",
            **(_shift_params(draft) if custom else {}),
        }))
    joining = [card for card in people if card.state in ("add", "transfer") and card.employee_id]
    if group_on and joining:
        steps.append(Step("add_to_group", {"department_id": group_id, "employee_ids": [card.employee_id for card in joining],
                                           "names": [card.name for card in joining]}))
    added = [card for card in joining if card.state == "add"]
    if added:
        steps.append(Step("assign", {"object_id": site_id, "employee_ids": [card.employee_id for card in added],
                                     "names": [card.name for card in added], "start_date": today.isoformat()}))
    for card in (card for card in joining if card.state == "transfer" and card.from_site_id):
        steps.append(Step("transfer", {
            "employee_id": card.employee_id, "name": card.name, "from_object_id": card.from_site_id,
            "from_name": by_site.get(card.from_site_id, {}).get("name", ""), "to_object_id": site_id,
            "from_date": (today + timedelta(days=1)).isoformat(),
        }))
    newcomers = [card for card in people if card.state == "new"]
    if newcomers:
        steps.append(Step("wait_for_photo", {"object_id": site_id, "names": [card.written for card in newcomers]}))

    clashes, unknown = (await _clashes(nazar, added, shift, sites, employees, site_id)) if shift else ((), ())
    preview = await _preview(nazar, draft, site_id, today, shift)
    return Plan(tuple(steps), missing, clashes, preview, site_name, draft.group.name if group_on else "", unknown)


def _missing(draft: Draft) -> tuple[tuple[Missing, str], ...]:
    out: list[tuple[Missing, str]] = []
    site = draft.site
    if site.state == "keep" and not site.existing_id:
        if not site.name:
            out.append(("site_name", ""))
        if not site.has_point:
            out.append(("point", ""))
        if not draft.shift.is_set:
            out.append(("shift", ""))
    if site.state == "skip" and not site.existing_id:
        out.append(("site", ""))
    out.extend(("choose_person", card.written) for card in draft.people if card.choices and card.state != "skip")
    if not (site.name or site.existing_id or draft.people or draft.group.name):
        out.append(("nothing", ""))
    return tuple(out)


def _site_params(draft: Draft) -> dict[str, Any]:
    site = draft.site
    return {
        "name": site.name, "address": site.address or site.name, "latitude": site.latitude, "longitude": site.longitude,
        "radius_meters": site.radius_meters, "work_schedule_mode": "custom", **_shift_params(draft),
    }


def _shift_params(draft: Draft) -> dict[str, Any]:
    return {"shift_start": draft.shift.start, "shift_end": draft.shift.end, "working_days": list(draft.shift.days)}


def _new_shift(draft: Draft, site: dict[str, Any] | None) -> Shift | None:
    """The shift people will work at the site: the draft's, or the existing site's own."""
    if draft.shift.is_set and draft.shift.state == "keep":
        return Shift(draft.shift.start, draft.shift.end, draft.shift.days)
    if site and site.get("shift_start") and site.get("shift_end"):
        return Shift(site["shift_start"], site["shift_end"], tuple(site.get("working_days") or ()))
    return None


async def _clashes(
    nazar: NazarClient, people: list[PersonCard], new: Shift, sites: list[dict[str, Any]],
    employees: list[dict[str, Any]], target: str,
) -> tuple[tuple[PersonClash, ...], tuple[str, ...]]:
    """Each person's shift on their other sites against the new one."""
    if not people:
        return (), ()
    wanted = {card.employee_id: card for card in people}
    others = [item for item in sites if item["id"] != target]
    lists = await asyncio.gather(*(call_nazar(nazar.get(f"/objects/{item['id']}/assignments")) for item in others))
    groups = await asyncio.gather(*(call_nazar(nazar.get(f"/objects/{item['id']}/groups")) for item in others))
    member_of = {item["id"]: {group["id"] for group in item.get("departments") or []} for item in employees}
    found: list[PersonClash] = []
    unknown: list[str] = []
    for site, listed, at_site in zip(others, lists, groups, strict=True):
        for entry in listed.get("assignments", []):
            card = wanted.get(entry["employee_id"])
            if card is None or not entry.get("is_active", True):
                continue
            theirs = _their_shift(entry, site, at_site.get("groups", []), member_of.get(card.employee_id, set()))
            if theirs is None:
                unknown.append(card.name)
                continue
            found_clash = clash(new, theirs)
            if found_clash:
                found.append(PersonClash(card, site["id"], site["name"], theirs, found_clash, tuple(options(new, [theirs]))))
    return tuple(found), tuple(dict.fromkeys(unknown))


def _their_shift(entry: dict[str, Any], site: dict[str, Any], groups: list[dict[str, Any]], member_of: set[str]) -> Shift | None:
    """Their own schedule there, else their group's there, else the site's; None if it is not weekly."""
    if entry.get("schedule_type") not in (None, "weekly"):
        return None
    if entry.get("schedule_mode") == "custom" and entry.get("shift_start") and entry.get("shift_end"):
        return Shift(entry["shift_start"], entry["shift_end"], tuple(entry.get("working_days") or site.get("working_days") or ()))
    for group in groups:
        if group["department_id"] in member_of and group.get("schedule_mode") == "custom":
            if group.get("schedule_type") not in (None, "weekly"):
                return None
            return Shift(group["shift_start"], group["shift_end"], tuple(group.get("working_days") or ()))
    if site.get("shift_start") and site.get("shift_end"):
        return Shift(site["shift_start"], site["shift_end"], tuple(site.get("working_days") or ()))
    return None


async def _preview(nazar: NazarClient, draft: Draft, site_id: str, today: date, shift: Shift | None) -> tuple[dict[str, Any], ...]:
    """The next days of the new shift: NAZAR's own preview for a site it has, the same weekly rule for a new one."""
    if shift is None:
        return ()
    if site_id != NEW_SITE:
        body = {"schedule_mode": "custom", "schedule_type": "weekly", "shift_start": shift.start, "shift_end": shift.end,
                "working_days": list(shift.days), "start_date": today.isoformat(), "days": PREVIEW_DAYS}
        shown = await call_nazar(nazar.post(f"/objects/{site_id}/schedule-preview", body))
        return tuple(
            {"date": day["date"], "working": day["is_working"], "from": _local(day.get("planned_start_at")),
             "to": _local(day.get("planned_end_at"))}
            for day in shown.get("days", [])
        )
    days = []
    for offset in range(PREVIEW_DAYS):
        day = today + timedelta(days=offset)
        working = day.isoweekday() in shift.days
        days.append({"date": day.isoformat(), "working": working, "from": shift.start if working else None,
                     "to": shift.end if working else None})
    return tuple(days)


def _local(value: str | None) -> str | None:
    if not value:
        return None
    return datetime.fromisoformat(value).astimezone(COMPANY_TZ).strftime("%H:%M")

