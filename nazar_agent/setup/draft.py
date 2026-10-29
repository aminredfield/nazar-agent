"""The setup being made in one conversation, as cards the owner can keep, change or skip.

Nothing in it exists in NAZAR until the owner applies the plan. Each message
of the conversation changes the same draft ("make the shift from 9", "not
Bobur"); the window's cards change it too. Cards are immutable: every change
gives a new draft.
"""

from __future__ import annotations

import uuid
from collections import OrderedDict
from dataclasses import dataclass, field, replace
from typing import Literal

DEFAULT_RADIUS = 100  # metres; NAZAR takes 10–1000
MAX_DRAFTS = 1000

CardState = Literal["keep", "skip"]
PersonState = Literal["add", "new", "transfer", "skip"]  # new: not in NAZAR yet, waits for a face photo


@dataclass(frozen=True, slots=True)
class SiteCard:
    name: str = ""
    address: str = ""
    latitude: float | None = None
    longitude: float | None = None
    point_source: str = ""  # google, yandex, 2gis, numbers, map, here
    radius_meters: int = DEFAULT_RADIUS
    existing_id: str = ""  # a site the company already has, chosen instead of a new one
    state: CardState = "keep"

    @property
    def has_point(self) -> bool:
        return self.latitude is not None and self.longitude is not None


@dataclass(frozen=True, slots=True)
class ShiftCard:
    start: str = ""  # HH:MM
    end: str = ""
    days: tuple[int, ...] = ()  # 1 = Monday … 7 = Sunday
    state: CardState = "keep"

    @property
    def is_set(self) -> bool:
        return bool(self.start and self.end and self.days)


@dataclass(frozen=True, slots=True)
class GroupCard:
    name: str = ""
    existing_id: str = ""
    state: CardState = "keep"


@dataclass(frozen=True, slots=True)
class PersonCard:
    written: str  # as the owner wrote it
    employee_id: str = ""  # found in NAZAR
    full_name: str = ""
    choices: tuple[tuple[str, str], ...] = ()  # (id, full name): several close names, the owner picks one
    state: PersonState = "add"
    from_site_id: str = ""  # for a transfer: the site the person leaves

    @property
    def name(self) -> str:
        return self.full_name or self.written


@dataclass(frozen=True, slots=True)
class Draft:
    id: str
    lang: str
    site: SiteCard = field(default_factory=SiteCard)
    shift: ShiftCard = field(default_factory=ShiftCard)
    group: GroupCard = field(default_factory=GroupCard)
    people: tuple[PersonCard, ...] = ()
    link_problem: str = ""  # a pasted link gave no point: not_a_map, no_point, unreachable

    @classmethod
    def new(cls, lang: str) -> Draft:
        return cls(id=uuid.uuid4().hex[:12], lang=lang)

    def person(self, written: str) -> PersonCard | None:
        key = written.casefold()
        return next((card for card in self.people if key in (card.written.casefold(), card.full_name.casefold())), None)

    def with_person(self, card: PersonCard) -> Draft:
        """The card added, or put in place of the one for the same person."""
        same = [i for i, other in enumerate(self.people) if _same(other, card)]
        if not same:
            return replace(self, people=(*self.people, card))
        people = list(self.people)
        people[same[0]] = card
        return replace(self, people=tuple(people))


def _same(one: PersonCard, other: PersonCard) -> bool:
    if one.employee_id and other.employee_id:
        return one.employee_id == other.employee_id
    return one.written.casefold() == other.written.casefold()


class Drafts:
    """The draft of each conversation; the oldest is dropped first."""

    def __init__(self) -> None:
        self._by_key: OrderedDict[str, Draft] = OrderedDict()

    def get(self, key: str) -> Draft | None:
        return self._by_key.get(key)

    def put(self, key: str, draft: Draft) -> None:
        self._by_key[key] = draft
        self._by_key.move_to_end(key)
        while len(self._by_key) > MAX_DRAFTS:
            self._by_key.popitem(last=False)

    def drop(self, key: str) -> None:
        self._by_key.pop(key, None)

    def clear(self) -> None:
        self._by_key.clear()
