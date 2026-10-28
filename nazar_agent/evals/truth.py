"""What is true in the demo company at a moment — the answers the question bank checks against.

Counted by code from the same demo world the agent reads, with the demo
NAZAR's own timesheet (``nazar_agent.demo.timesheet``), never by a model.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from datetime import date, datetime, timedelta
from typing import Any

from nazar_agent.demo.timesheet import Book, day_row, local_day, plan_of
from nazar_agent.demo.world import LATE_GRACE_MINUTES, TZ, Person, World


class Truth:
    def __init__(self, world: World) -> None:
        self.world = world
        self.book = Book()  # the bank applies nothing: the company as built
        self.today = world.today
        self.now = world.now.astimezone(TZ)

    # Dates as an owner says them ---------------------------------------------

    @property
    def yesterday(self) -> date:
        return self.today - timedelta(days=1)

    def last_week(self) -> tuple[date, date]:
        monday = self.today - timedelta(days=self.today.isoweekday() - 1)
        return monday - timedelta(days=7), monday - timedelta(days=1)

    def last(self, weekday: int) -> date:
        """The latest past day with this ISO weekday (1 = Monday), not today."""
        back = (self.today.isoweekday() - weekday) % 7 or 7
        return self.today - timedelta(days=back)

    def two_weeks(self) -> tuple[date, date]:
        return self.today - timedelta(days=14), self.yesterday

    # People and sites ----------------------------------------------------------

    def person(self, name: str) -> Person:
        return next(person for person in self.world.people.values() if person.name == name)

    def site_name(self, key: str) -> str:
        return next(site.name for site in self.world.sites.values() if site.key == key)

    def site_of(self, person: Person) -> str:
        return self.world.sites[person.site_id].name if person.site_id else ""

    # Counted facts ---------------------------------------------------------------

    def rows(self, first: date, last: date) -> Iterator[tuple[Person, dict[str, Any]]]:
        day = first
        while day <= last:
            for person in sorted(self.world.people.values(), key=lambda item: item.code):
                yield person, day_row(self.world, self.book, person, day)
            day += timedelta(days=1)

    def check_ins(self, person: Person, day: date) -> list[datetime]:
        return [
            event.at.astimezone(TZ)
            for event in self.world.events_of(person.id)
            if event.event_type == "check_in" and local_day(event.at) == day
        ]

    def absent(self, day: date) -> list[str]:
        """Planned and not checked in; today only once the shift and its grace have started."""
        names = []
        for person in self.world.people.values():
            plan = plan_of(self.world, person, day)
            if plan is None or (day == self.today and self.now < plan[0] + timedelta(minutes=LATE_GRACE_MINUTES)):
                continue
            if not self.check_ins(person, day):
                names.append(person.name)
        return sorted(names)

    def late(self, day: date) -> list[str]:
        """Arrived past the start and its grace — today too, from the check-in itself."""
        names = []
        for person in self.world.people.values():
            plan = plan_of(self.world, person, day)
            arrivals = self.check_ins(person, day)
            if plan and arrivals and min(arrivals) > plan[0] + timedelta(minutes=LATE_GRACE_MINUTES):
                names.append(person.name)
        return sorted(names)

    def on_site_now(self, site_key: str | None = None) -> list[str]:
        names = []
        for person in self.world.people.values():
            events = self.world.events_of(person.id)
            if events and events[-1].event_type == "check_in" and local_day(events[-1].at) == self.today:
                if site_key is None or self.world.sites[events[-1].object_id].key == site_key:
                    names.append(person.name)
        return sorted(names)

    def worked_minutes(self, name: str, first: date, last: date) -> int:
        return sum(row["worked_minutes"] for person, row in self.rows(first, last) if person.name == name)

    def site_worked_minutes(self, site_key: str, day: date) -> int:
        return sum(
            row["worked_minutes"] for person, row in self.rows(day, day)
            if person.site_id and self.world.sites[person.site_id].key == site_key
        )

    def counts(self, first: date, last: date, what: str) -> Counter[str]:
        """Per person: days late, days absent, or minutes of overtime / work."""
        counted: Counter[str] = Counter()
        for person, row in self.rows(first, last):
            if what == "late_days":
                counted[person.name] += row["late_minutes"] > 0
            elif what == "absent_days":
                counted[person.name] += row["day_code"] == "absent"
            else:
                counted[person.name] += row[what]
        return counted

    def top(self, counted: Counter[str]) -> list[str]:
        """Everyone tied for the first place."""
        if not counted:
            return []
        best = max(counted.values())
        return sorted(name for name, value in counted.items() if value == best and value > 0)

    def site_absences(self, first: date, last: date) -> Counter[str]:
        return Counter(self.site_of(person) for person, row in self.rows(first, last) if row["day_code"] == "absent")
