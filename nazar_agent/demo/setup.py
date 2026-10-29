"""Setting the demo company up, as NAZAR does: new sites, groups, a group's schedule at a site, members.

The routes answer with NAZAR's fields (recorded in ``tests/contracts``):
``POST/DELETE /objects``, ``/departments`` with ``bulk-add`` and
``bulk-remove``, ``/objects/{id}/groups``, ``/objects/{id}/schedule-preview``.
A person may now work on a second site too, as in NAZAR, where assigning to
another object adds to the first one. Everything lives in memory and goes
with the demo's daily reset.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from nazar_agent.demo.timesheet import iso
from nazar_agent.demo.world import SITE_SCHEDULE, TZ, Schedule, Site, World, stable_id

CLOCK = r"^([01]\d|2[0-3]):[0-5]\d$"
Weekday = Annotated[int, Field(ge=1, le=7)]
Color = Literal["blue", "green", "amber", "red", "violet", "slate"]


class SetupError(Exception):
    """A request NAZAR would refuse: the status and the message, as the server answers them."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


# --- Request bodies, as NAZAR takes them ---------------------------------------


class SiteCreate(BaseModel):
    name: str = Field(min_length=2, max_length=255)
    address: str = Field(min_length=3, max_length=500)
    latitude: float = Field(ge=-90, le=90)
    longitude: float = Field(ge=-180, le=180)
    radius_meters: float = Field(gt=10, le=1000)
    is_active: bool = True
    shift_start: str | None = Field(default=None, pattern=CLOCK)
    shift_end: str | None = Field(default=None, pattern=CLOCK)
    work_schedule_mode: Literal["company_default", "custom", "unset"] = "company_default"
    working_days: list[Weekday] = Field(default_factory=lambda: list(SITE_SCHEDULE[2]), min_length=1, max_length=7)


class DepartmentCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    color: Color = "blue"


class Membership(BaseModel):
    employee_ids: list[uuid.UUID] = Field(min_length=1, max_length=500)
    department_ids: list[uuid.UUID] = Field(min_length=1, max_length=50)
    effective_from: date | None = None


class GroupSchedule(BaseModel):
    department_id: uuid.UUID
    schedule_mode: Literal["object_default", "custom"] = "object_default"
    shift_start: str | None = Field(default=None, pattern=CLOCK)
    shift_end: str | None = Field(default=None, pattern=CLOCK)
    working_days: list[Weekday] | None = Field(default=None, min_length=1, max_length=7)
    schedule_type: Literal["weekly"] | None = None


class SchedulePreview(BaseModel):
    schedule_mode: Literal["object_default", "custom"] = "custom"
    schedule_type: Literal["weekly"] | None = None
    shift_start: str | None = Field(default=None, pattern=CLOCK)
    shift_end: str | None = Field(default=None, pattern=CLOCK)
    working_days: list[Weekday] | None = None
    start_date: date | None = None
    days: int = Field(default=14, ge=1, le=62)


# --- State ---------------------------------------------------------------------


@dataclass
class Department:
    id: str
    name: str
    color: str
    created_at: datetime
    members: dict[str, date] = field(default_factory=dict)  # employee → member since


@dataclass
class GroupAt:
    """A group working at a site, with the schedule it follows there."""

    department_id: str
    schedule_mode: str
    shift_start: str | None
    shift_end: str | None
    working_days: list[int] | None
    assigned_at: datetime


@dataclass
class Setup:
    departments: dict[str, Department] = field(default_factory=dict)
    groups: dict[str, dict[str, GroupAt]] = field(default_factory=dict)  # site → department → group at it


# The crews the company starts with: a group per site, everyone on it a member.
CREWS = (("chilonzor", "Chilonzor crew", "blue"), ("sergeli", "Sergeli crew", "green"), ("yashnobod", "Yashnobod finishing crew", "amber"))


def crews(world: World) -> Setup:
    """The demo's groups as of the first day: one crew per site, not tied to a site's schedule."""
    setup = Setup()
    sites = {site.key: site for site in world.sites.values()}
    for key, name, color in CREWS:
        site = sites[key]
        item = Department(id=stable_id("group", key), name=name, color=color, created_at=site.created_at)
        item.members = {person.id: person.assigned_from for person in world.people.values() if person.site_id == site.id}
        setup.departments[item.id] = item
    return setup


# --- Sites ---------------------------------------------------------------------


def create_site(world: World, body: SiteCreate, now: datetime) -> Site:
    if (body.shift_start is None) != (body.shift_end is None):
        raise SetupError(422, "shift_start and shift_end must be set together")
    start, end = body.shift_start or SITE_SCHEDULE[0], body.shift_end or SITE_SCHEDULE[1]
    if start == end:
        raise SetupError(422, "The shift must not start and end at the same time.")
    site = Site(
        id=str(uuid.uuid4()), key=f"new-{len(world.sites) + 1}", name=body.name.strip(), address=body.address.strip(),
        latitude=body.latitude, longitude=body.longitude, radius_meters=body.radius_meters, created_at=now,
        schedules=[Schedule(world.today, start, end, tuple(sorted(set(body.working_days))), body.work_schedule_mode)],
        is_active=body.is_active,
    )
    world.sites[site.id] = site
    return site


def delete_site(world: World, setup: Setup, site_id: str) -> None:
    """NAZAR deletes the object; one with check-ins on it is refused here, its history is the demo's story."""
    if any(event.object_id == site_id for event in world.timeline):
        raise SetupError(409, "The object has check-ins; switch it off instead.")
    world.sites.pop(site_id)
    setup.groups.pop(site_id, None)
    for person in world.people.values():
        if person.site_id == site_id:
            person.site_id = None
    for extra in world.extra_sites.values():
        extra.pop(site_id, None)


def preview(site: Site, body: SchedulePreview, today: date) -> dict[str, Any]:
    """The next days of a weekly schedule: the site's own, or the one being considered."""
    own = site.schedule_on(today)
    custom = body.schedule_mode == "custom"
    start = (body.shift_start if custom else None) or own.shift_start
    end = (body.shift_end if custom else None) or own.shift_end
    weekdays = sorted(set((body.working_days if custom else None) or own.working_days))
    first = body.start_date or today
    minutes = _minutes(start, end)
    days = []
    for offset in range(body.days):
        day = first + timedelta(days=offset)
        working = day.isoweekday() in weekdays
        begins = datetime.combine(day, time.fromisoformat(start), tzinfo=TZ)
        days.append({
            "date": day.isoformat(), "is_working": working,
            "planned_start_at": iso(begins) if working else None,
            "planned_end_at": iso(begins + timedelta(minutes=minutes)) if working else None,
            "planned_minutes": minutes if working else 0, "is_24_hours": False,
            "schedule_source": "employee_weekly" if custom else "object_default", "exception_reason": None,
        })
    return {
        "schedule_type": "weekly", "schedule_label": "weekly:" + ",".join(map(str, weekdays)),
        "is_24_hours": False, "shift_duration_minutes": minutes, "days": days,
    }


def _minutes(start: str, end: str) -> int:
    begin, finish = (int(clock[:2]) * 60 + int(clock[3:]) for clock in (start, end))
    return (finish - begin) % (24 * 60) or 24 * 60  # an end before the start is the next morning


# --- Groups --------------------------------------------------------------------


def department_view(item: Department) -> dict[str, Any]:
    return {
        "id": item.id, "name": item.name, "color": item.color, "employee_count": len(item.members),
        "created_at": iso(item.created_at), "updated_at": iso(item.created_at),
    }


def create_department(setup: Setup, body: DepartmentCreate, now: datetime) -> Department:
    name = body.name.strip()
    if any(item.name.casefold() == name.casefold() for item in setup.departments.values()):
        raise SetupError(409, "A group with this name already exists.")
    item = Department(id=str(uuid.uuid4()), name=name, color=body.color, created_at=now)
    setup.departments[item.id] = item
    return item


def delete_department(setup: Setup, department_id: str) -> None:
    if setup.departments.pop(department_id, None) is None:
        raise SetupError(404, "Department not found.")
    for groups in setup.groups.values():
        groups.pop(department_id, None)


def membership(world: World, setup: Setup, body: Membership, *, add: bool, today: date) -> dict[str, Any]:
    people = [str(item) for item in body.employee_ids]
    departments = [_department(setup, str(item)) for item in body.department_ids]
    if any(person not in world.people for person in people):
        raise SetupError(404, "Employee not found.")
    since = body.effective_from or today
    changed = 0
    for department in departments:
        for person in people:
            if add and person not in department.members:
                department.members[person], changed = since, changed + 1
            elif not add and person in department.members:
                del department.members[person]
                changed += 1
    return {"changed_count": changed, "effective_from": since.isoformat()}


def groups_view(world: World, setup: Setup, site_id: str) -> dict[str, Any]:
    working_here = {person.id for person in world.people.values() if site_id in world.sites_of(person.id)}
    groups = []
    for group in setup.groups.get(site_id, {}).values():
        department = setup.departments[group.department_id]
        groups.append({
            "department_id": department.id, "name": department.name, "color": department.color,
            "employee_count": len(working_here & department.members.keys()), "schedule_mode": group.schedule_mode,
            "shift_start": group.shift_start, "shift_end": group.shift_end, "working_days": group.working_days,
            "schedule_type": "weekly" if group.schedule_mode == "custom" else None,
            "cycle_anchor_date": None, "cycle_pattern": None, "assigned_at": iso(group.assigned_at),
        })
    return {"object_id": site_id, "groups": groups}


def set_group(setup: Setup, site_id: str, body: GroupSchedule, now: datetime) -> None:
    department = _department(setup, str(body.department_id))
    custom = body.schedule_mode == "custom"
    if custom and (body.shift_start is None or body.shift_end is None or not body.working_days):
        raise SetupError(422, "custom group schedule requires start, end and working days")
    previous = setup.groups.get(site_id, {}).get(department.id)
    setup.groups.setdefault(site_id, {})[department.id] = GroupAt(
        department_id=department.id, schedule_mode=body.schedule_mode,
        shift_start=body.shift_start if custom else None, shift_end=body.shift_end if custom else None,
        working_days=sorted(set(body.working_days)) if custom and body.working_days else None,
        assigned_at=previous.assigned_at if previous else now,
    )


def remove_group(setup: Setup, site_id: str, department_id: str) -> None:
    if setup.groups.get(site_id, {}).pop(department_id, None) is None:
        raise SetupError(404, "The group does not work at this object.")


def groups_of(setup: Setup, person_id: str) -> list[dict[str, str]]:
    """The groups on an employee's card: id, name, colour."""
    return [
        {"id": item.id, "name": item.name, "color": item.color}
        for item in setup.departments.values() if person_id in item.members
    ]


def _department(setup: Setup, department_id: str) -> Department:
    item = setup.departments.get(department_id)
    if item is None:
        raise SetupError(404, "Department not found.")
    return item
