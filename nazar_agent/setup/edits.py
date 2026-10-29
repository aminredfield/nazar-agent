"""The owner's changes to a draft from the window's cards, checked before they touch it.

A field changed, a point put on the map or "I am here", an existing site or
group chosen, a person chosen among close names, left out or brought back,
someone added by name. Every id must be one NAZAR has; every value is in
NAZAR's own limits.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from nazar_agent.setup.draft import Draft, GroupCard, PersonCard
from nazar_agent.setup.reading import capital, person_card

CLOCK = r"^([01]\d|2[0-3]):[0-5]\d$"
Weekday = Annotated[int, Field(ge=1, le=7)]


class EditError(Exception):
    """An edit that names what NAZAR does not have, or a card the draft does not have."""


class _Edit(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SiteEdit(_Edit):
    name: str | None = Field(default=None, min_length=2, max_length=255)
    address: str | None = Field(default=None, max_length=500)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    point_source: Literal["map", "here", "google", "yandex", "2gis", "numbers"] | None = None
    radius_meters: int | None = Field(default=None, gt=10, le=1000)
    existing_id: str | None = None  # "" — a new site after all
    state: Literal["keep", "skip"] | None = None

    @model_validator(mode="after")
    def _point_whole(self) -> SiteEdit:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("a point needs both latitude and longitude")
        return self


class ShiftEdit(_Edit):
    start: str | None = Field(default=None, pattern=CLOCK)
    end: str | None = Field(default=None, pattern=CLOCK)
    days: list[Weekday] | None = Field(default=None, min_length=1, max_length=7)
    state: Literal["keep", "skip"] | None = None


class GroupEdit(_Edit):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    existing_id: str | None = None
    state: Literal["keep", "skip"] | None = None


class PersonEdit(_Edit):
    written: str = Field(min_length=1, max_length=200)  # which card: as the owner wrote the name
    employee_id: str | None = None  # the one chosen among close names
    state: Literal["add", "new", "skip"] | None = None


class SetupEdit(_Edit):
    draft_id: str
    site: SiteEdit | None = None
    shift: ShiftEdit | None = None
    group: GroupEdit | None = None
    person: PersonEdit | None = None
    add_people: list[Annotated[str, Field(min_length=2, max_length=200)]] = Field(default_factory=list, max_length=50)


def edit(draft: Draft, change: SetupEdit, *, employees: list[dict[str, Any]], sites: list[dict[str, Any]], groups: list[dict[str, Any]]) -> Draft:
    if change.draft_id != draft.id:
        raise EditError("The draft changed; open it again.")
    if change.site:
        draft = replace(draft, site=_site(draft, change.site, sites))
    if change.shift:
        shift = change.shift
        draft = replace(draft, shift=replace(
            draft.shift, start=shift.start or draft.shift.start, end=shift.end or draft.shift.end,
            days=tuple(sorted(set(shift.days))) if shift.days else draft.shift.days, state=shift.state or draft.shift.state,
        ))
        if draft.shift.start and draft.shift.start == draft.shift.end:
            raise EditError("The shift must not start and end at the same time.")
    if change.group:
        draft = replace(draft, group=_group(draft.group, change.group, groups))
    if change.person:
        draft = _person_edit(draft, change.person, employees)
    for name in change.add_people:
        if draft.person(name.strip()) is None:
            draft = draft.with_person(person_card(name.strip(), employees))
    return draft


def _site(draft: Draft, change: SiteEdit, sites: list[dict[str, Any]]):
    card = draft.site
    if change.existing_id is not None:
        if change.existing_id:
            known = next((item for item in sites if item["id"] == change.existing_id), None)
            if known is None:
                raise EditError("No such site.")
            card = replace(card, existing_id=known["id"], name=known["name"])
        else:
            card = replace(card, existing_id="")
    values = {
        "name": capital(change.name.strip()) if change.name else None, "address": change.address,
        "radius_meters": change.radius_meters, "state": change.state,
    }
    card = replace(card, **{key: value for key, value in values.items() if value is not None})
    if change.latitude is not None and change.longitude is not None:
        card = replace(card, latitude=change.latitude, longitude=change.longitude, point_source=change.point_source or "map")
    return card


def _group(card: GroupCard, change: GroupEdit, groups: list[dict[str, Any]]) -> GroupCard:
    if change.existing_id:
        known = next((item for item in groups if item["id"] == change.existing_id), None)
        if known is None:
            raise EditError("No such group.")
        card = GroupCard(name=known["name"], existing_id=known["id"])
    elif change.name:
        card = GroupCard(name=capital(change.name.strip()))
    return replace(card, state=change.state or card.state)


def _person_edit(draft: Draft, change: PersonEdit, employees: list[dict[str, Any]]) -> Draft:
    card = draft.person(change.written)
    if card is None:
        raise EditError("No such person in the plan.")
    if change.employee_id:
        known = next((item for item in employees if item["id"] == change.employee_id), None)
        if known is None:
            raise EditError("No such employee.")
        card = PersonCard(written=card.written, employee_id=known["id"], full_name=known.get("full_name") or "")
    if change.state:
        if change.state in ("add",) and not card.employee_id:
            raise EditError("Choose who it is first.")
        card = replace(card, state=change.state)
    return draft.with_person(card)
