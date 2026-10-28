"""The demo construction company the demo NAZAR serves: people, sites, three weeks of check-ins.

Mustahkam Qurilish — the same company as NAZAR's construction demo, built in
memory so the agent runs without NAZAR. Generated from a seed, so the same
seed and the same moment give the same world; "today" is the current
Tashkent day, with today's check-ins up to now. The cases the agents are
evaluated on:

- Chilonzor — four workers left ``left_days_ago`` days ago, but the foreman
  keeps checking them in and out from his own phone: one device, a minute
  apart, their registration photo on a phone screen in his cabin. Before they
  left, each checked in from their own phone with a selfie;
- Chilonzor — a worker whose phone gives a weak GPS: flags, twice outside the
  fence, but live selfies and full days. Technology, not fraud;
- Sergeli — two workers without smartphones, checked in by their honest
  foreman from his phone: live selfies, on the site, each checks out on their own;
- Yashnobod — a finishing crew works 09:00–19:00 while the site's schedule
  says 08:00–18:00. The schedule is wrong, not the people.

Everyone's assignment starts on the first generated day: no day before the
history counts as missed (NAZAR's seed had that off by one).
"""

from __future__ import annotations

import hashlib
import math
import random
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Asia/Tashkent")
NAMESPACE = uuid.UUID("6f0f3c1e-8a52-4d1b-9c55-2b7d2a1f9e10")
COMPANY_NAME = "Mustahkam Qurilish"
OWNER_NAME = "Демо Стройка"
LATE_GRACE_MINUTES = 10
ABSENCE_CHANCE = 0.04
LATE_CHANCE = 0.1
# The weak-GPS worker drifts outside the fence on the 3rd and 8th working day back.
WEAK_GPS_OUTSIDE_WORKDAYS_AGO = (3, 8)
USER_AGENTS = (
    "Mozilla/5.0 (Linux; Android 14; SM-A546E) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36",
    "Mozilla/5.0 (Linux; Android 13; Redmi Note 12) AppleWebKit/537.36 Chrome/125.0 Mobile Safari/537.36",
    "Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 Mobile/15E148",
    "Mozilla/5.0 (Linux; Android 14; Pixel 7a) AppleWebKit/537.36 Chrome/126.0 Mobile Safari/537.36",
)

Kind = Literal["foreman", "worker", "left", "weak_gps", "foreman_honest", "no_phone", "late_crew"]
EventType = Literal["check_in", "check_out"]


def stable_id(*parts: object) -> str:
    """The same record gets the same id in every run of the same world."""
    return str(uuid.uuid5(NAMESPACE, "|".join(str(part) for part in parts)))


@dataclass
class Schedule:
    effective_from: date
    shift_start: str  # HH:MM
    shift_end: str
    working_days: tuple[int, ...]  # 1 = Monday … 7 = Sunday
    mode: str = "company_default"


@dataclass
class Site:
    id: str
    key: str
    name: str
    address: str
    latitude: float
    longitude: float
    radius_meters: float
    created_at: datetime
    # Schedules by date, as NAZAR keeps them: a change applies from its day on.
    schedules: list[Schedule] = field(default_factory=list)
    is_active: bool = True

    def schedule_on(self, day: date) -> Schedule:
        current = [item for item in self.schedules if item.effective_from <= day]
        return current[-1] if current else self.schedules[0]

    def shift_on(self, day: date) -> tuple[datetime, datetime] | None:
        """The planned shift of that local day, or None on a day off."""
        schedule = self.schedule_on(day)
        if day.isoweekday() not in schedule.working_days:
            return None
        start = datetime.combine(day, time.fromisoformat(schedule.shift_start), tzinfo=TZ)
        end = datetime.combine(day, time.fromisoformat(schedule.shift_end), tzinfo=TZ)
        return start, end if end > start else end + timedelta(days=1)


@dataclass
class Person:
    id: str
    name: str
    code: str  # NAZAR's employee number
    site_id: str
    kind: Kind
    face: str
    created_at: datetime
    assigned_from: date
    is_active: bool = True


@dataclass
class Event:
    id: str
    employee_id: str
    object_id: str
    event_type: EventType
    at: datetime  # UTC
    face: str
    photo_kind: Literal["live", "screen"]
    photo_seed: int
    similarity: float
    latitude: float
    longitude: float
    accuracy: float
    inside: bool
    risk_flags: tuple[str, ...]
    device_key: str
    user_agent: str
    request_ip: str
    check_in_id: str | None = None  # a check-out names its check-in
    review_status: str = "not_reviewed"
    review_comment: str | None = None
    reviewed_by: str | None = None
    reviewed_at: datetime | None = None


@dataclass
class World:
    company_id: str
    now: datetime  # UTC
    first_day: date
    sites: dict[str, Site]
    people: dict[str, Person]
    events: list[Event]

    @property
    def today(self) -> date:
        return self.now.astimezone(TZ).date()


SITES = (
    ("chilonzor", "ЖК «Чиланзар Сити», блок 3", "г. Ташкент, Чиланзарский район, ул. Арнасой, 7", 41.28032, 69.19115, 150.0),
    ("sergeli", "Склад-ангар, Сергели", "г. Ташкент, Сергелийский район, ул. Янги Сергели, 42", 41.22681, 69.21904, 120.0),
    ("yashnobod", "ЖК «Яшнабад», отделка", "г. Ташкент, Яшнабадский район, ул. Махтумкули, 95", 41.30692, 69.33641, 100.0),
)
# Every site is set to 08:00–18:00, six days a week. For Yashnobod that is the mistake to find.
SITE_SCHEDULE = ("08:00", "18:00", (1, 2, 3, 4, 5, 6))
ROSTER: tuple[tuple[str, str, str, Kind], ...] = (
    ("Рустам Юлдашев", "rustam_yuldashev.jpg", "chilonzor", "foreman"),
    ("Азамат Норматов", "azamat_normatov.jpg", "chilonzor", "worker"),
    ("Бобур Каримов", "bobur_karimov.jpg", "chilonzor", "worker"),
    ("Илхом Тураев", "ilhom_turaev.jpg", "chilonzor", "worker"),
    ("Нодира Сафарова", "nodira_safarova.jpg", "chilonzor", "worker"),
    ("Шерзод Алимов", "sherzod_alimov.jpg", "chilonzor", "weak_gps"),
    ("Тимур Хасанов", "timur_hasanov.jpg", "chilonzor", "left"),
    ("Гулчехра Ибрагимова", "gulchehra_ibragimova.jpg", "chilonzor", "left"),
    ("Мохира Юнусова", "mohira_yunusova.jpg", "chilonzor", "left"),
    ("Феруза Каримова", "feruza_karimova.jpg", "chilonzor", "left"),
    ("Дилшод Мирзаев", "dilshod_mirzaev.jpg", "sergeli", "foreman_honest"),
    ("Хуршид Рахмонов", "hurshid_rahmonov.jpg", "sergeli", "no_phone"),
    ("Анвар Бакиров", "anvar_bakirov.jpg", "sergeli", "no_phone"),
    ("Зухра Набиева", "zuhra_nabieva.jpg", "yashnobod", "late_crew"),
    ("Камола Ташпулатова", "kamola_tashpulatova.jpg", "yashnobod", "late_crew"),
    ("Дилфуза Эргашева", "dilfuza_ergasheva.jpg", "yashnobod", "late_crew"),
)


def phone_key(owner: str) -> str:
    """The device key NAZAR shows (change 0003): the first 16 characters of the phone's hash."""
    return hashlib.sha256(f"mustahkam-phone|{owner}".encode()).hexdigest()[:16]


def build_world(now: datetime | None = None, *, seed: int = 20261028, days: int = 21, left_days_ago: int = 10) -> World:
    now = now or datetime.now(TZ)
    now_utc = now.astimezone(ZoneInfo("UTC"))
    today = now.astimezone(TZ).date()
    first_day = today - timedelta(days=days)
    origin = datetime.combine(first_day - timedelta(days=1), time(8, 0), tzinfo=TZ)
    company_id = stable_id("company", COMPANY_NAME)

    sites: dict[str, Site] = {}
    for key, name, address, lat, lon, radius in SITES:
        site = Site(stable_id("site", key), key, name, address, lat, lon, radius, origin)
        site.schedules.append(Schedule(first_day, SITE_SCHEDULE[0], SITE_SCHEDULE[1], SITE_SCHEDULE[2]))
        sites[key] = site
    people = {
        person.id: person
        for index, (name, face, site_key, kind) in enumerate(ROSTER)
        for person in [Person(stable_id("person", name), name, f"MQ-{index + 1:03d}", sites[site_key].id, kind, face, origin, first_day)]
    }
    builder = _Builder(random.Random(seed), now_utc, {site.id: site for site in sites.values()})
    workdays_back = [
        day for day in (today - timedelta(days=n) for n in range(1, days + 1)) if sites["chilonzor"].shift_on(day)
    ]
    builder.weak_gps_outside = {workdays_back[n - 1] for n in WEAK_GPS_OUTSIDE_WORKDAYS_AGO if n <= len(workdays_back)}
    for offset in range(days, -1, -1):
        builder.day(list(people.values()), today - timedelta(days=offset), offset, left_days_ago)
    events = sorted(builder.events, key=lambda event: event.at)
    return World(company_id, now_utc, first_day, {site.id: site for site in sites.values()}, people, events)


class _Builder:
    """Adds check-ins and check-outs with their photo, phone and GPS, as people would."""

    def __init__(self, rng: random.Random, now_utc: datetime, sites: dict[str, Site]) -> None:
        self.rng = rng
        self.now_utc = now_utc
        self.sites = sites
        self.events: list[Event] = []
        self.user_agents: dict[str, str] = {}
        self.weak_gps_outside: set[date] = set()

    def add(
        self, person: Person, event_type: EventType, at: datetime, *, phone_owner: str, photo_kind: str = "live",
        similarity: float, check_in: Event | None = None, accuracy: float | None = None,
        lat: float | None = None, lon: float | None = None, inside: bool = True, risk_flags: tuple[str, ...] = (),
    ) -> Event | None:
        at_utc = at.astimezone(ZoneInfo("UTC"))
        if at_utc > self.now_utc:  # the future has not happened yet
            return None
        site = self.sites[person.site_id]
        if lat is None or lon is None:
            lat, lon = self.jitter(site.latitude, site.longitude, site.radius_meters * 0.5)
        device = phone_key(phone_owner)
        event = Event(
            id=stable_id("event", person.id, event_type, at_utc.isoformat()),
            employee_id=person.id, object_id=site.id, event_type=event_type, at=at_utc, face=person.face,
            photo_kind=photo_kind, photo_seed=self.rng.randrange(1 << 30), similarity=round(similarity, 4),
            latitude=lat, longitude=lon, accuracy=round(accuracy if accuracy is not None else self.rng.uniform(5, 25), 1),
            inside=inside, risk_flags=risk_flags, device_key=device,
            user_agent=self.user_agents.setdefault(device, self.rng.choice(USER_AGENTS)),
            request_ip=f"84.54.{self.rng.randint(64, 127)}.{self.rng.randint(2, 250)}",
            check_in_id=check_in.id if check_in else None,
        )
        self.events.append(event)
        return event

    def jitter(self, lat: float, lon: float, meters: float) -> tuple[float, float]:
        offset = meters / 111_000  # ~1e-5 degrees is 1.1 m; keep the point well inside the fence
        return round(lat + self.rng.uniform(-offset, offset), 6), round(lon + self.rng.uniform(-offset, offset), 6)

    def own_phone(self, person: Person, shift: tuple[datetime, datetime], *, arrive: float, leave: float, **extra) -> None:
        """Someone with a smartphone: their own phone, a selfie, check-out on their own."""
        check_in = self.add(person, "check_in", shift[0] + timedelta(minutes=arrive), phone_owner=person.name,
                            similarity=self.rng.uniform(0.72, 0.9), **extra)
        if check_in:
            self.add(person, "check_out", shift[1] + timedelta(minutes=leave), phone_owner=person.name,
                     similarity=self.rng.uniform(0.72, 0.9), check_in=check_in, **extra)

    def regular_times(self) -> tuple[float, float]:
        late = self.rng.random() < LATE_CHANCE
        return (self.rng.uniform(12, 30) if late else self.rng.uniform(-12, 6)), self.rng.uniform(-15, -2)

    def dead_souls(self, foreman: Person, souls: list[tuple[Person, tuple[datetime, datetime]]], foreman_in: datetime) -> None:
        """The foreman checks in, then out, people who no longer come — from his cabin."""
        site = self.sites[foreman.site_id]
        cabin = (site.latitude + 0.00022, site.longitude - 0.00031)
        at = foreman_in + timedelta(minutes=self.rng.uniform(2, 4))
        check_ins: dict[str, Event] = {}
        for person, _ in souls:
            lat, lon = self.jitter(*cabin, 2)
            event = self.add(person, "check_in", at, phone_owner=foreman.name, photo_kind="screen",
                             similarity=self.rng.uniform(0.55, 0.63), accuracy=self.rng.uniform(6, 10), lat=lat, lon=lon)
            if event:
                check_ins[person.id] = event
            at += timedelta(seconds=self.rng.uniform(40, 75))
        if not souls:
            return
        at = souls[0][1][1] - timedelta(minutes=self.rng.uniform(4, 6))
        for person, _ in souls:
            if person.id in check_ins:
                lat, lon = self.jitter(*cabin, 2)
                self.add(person, "check_out", at, phone_owner=foreman.name, photo_kind="screen",
                         similarity=self.rng.uniform(0.55, 0.63), accuracy=self.rng.uniform(6, 10), lat=lat, lon=lon,
                         check_in=check_ins[person.id])
            at += timedelta(seconds=self.rng.uniform(30, 60))

    def without_smartphones(self, foreman: Person, crew: list[tuple[Person, tuple[datetime, datetime]]], foreman_in: datetime) -> None:
        """The honest foreman checks in his crew one by one: each on the site, each a selfie."""
        at = foreman_in + timedelta(minutes=self.rng.uniform(2, 5))
        for person, shift in crew:
            check_in = self.add(person, "check_in", at, phone_owner=foreman.name, similarity=self.rng.uniform(0.74, 0.88))
            if check_in:
                self.add(person, "check_out", shift[1] - timedelta(minutes=self.rng.uniform(2, 18)), phone_owner=foreman.name,
                         similarity=self.rng.uniform(0.74, 0.88), check_in=check_in)
            at += timedelta(seconds=self.rng.uniform(70, 200))

    def weak_gps(self, person: Person, shift: tuple[datetime, datetime]) -> None:
        arrive, leave = self.regular_times()
        site = self.sites[person.site_id]
        if shift[0].date() in self.weak_gps_outside:
            # The position jumps ~250 m off the site; the selfie and the hours are normal.
            self.own_phone(person, shift, arrive=arrive, leave=leave, accuracy=self.rng.uniform(160, 240),
                           lat=site.latitude + 0.0021, lon=site.longitude + 0.0012, inside=False,
                           risk_flags=("accuracy_too_low", "gps_accuracy_warning"))
            return
        self.own_phone(person, shift, arrive=arrive, leave=leave, accuracy=self.rng.uniform(90, 150),
                       risk_flags=("gps_accuracy_warning",))

    def day(self, people: list[Person], day: date, offset: int, left_days_ago: int) -> None:
        by_kind: dict[str, list[tuple[Person, tuple[datetime, datetime]]]] = {}
        for person in people:
            shift = self.sites[person.site_id].shift_on(day)
            if shift is not None and day >= person.assigned_from:
                by_kind.setdefault(person.kind, []).append((person, shift))
        gone = offset <= left_days_ago

        for kind, helped in (("foreman", "left"), ("foreman_honest", "no_phone")):
            for foreman, shift in by_kind.get(kind, []):
                arrive = -self.rng.uniform(10, 18)
                self.own_phone(foreman, shift, arrive=arrive, leave=self.rng.uniform(1, 3))
                foreman_in = shift[0] + timedelta(minutes=arrive)
                crew = by_kind.get(helped, [])
                if helped == "left" and gone:
                    self.dead_souls(foreman, crew, foreman_in)
                elif helped == "no_phone":
                    self.without_smartphones(foreman, [item for item in crew if self.rng.random() > ABSENCE_CHANCE], foreman_in)

        for person, shift in by_kind.get("worker", []) + ([] if gone else by_kind.get("left", [])):
            if self.rng.random() < ABSENCE_CHANCE:
                continue
            arrive, leave = self.regular_times()
            self.own_phone(person, shift, arrive=arrive, leave=leave)
        for person, shift in by_kind.get("weak_gps", []):
            self.weak_gps(person, shift)
        for person, shift in by_kind.get("late_crew", []):
            # They work 09:00–19:00; the site says 08:00–18:00.
            self.own_phone(person, shift, arrive=60 + self.rng.uniform(-6, 8), leave=60 + self.rng.uniform(-4, 10))


def distance_meters(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Great-circle distance, for an event's distance to its site."""
    radius = 6_371_000
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    d_phi, d_lambda = math.radians(lat2 - lat1), math.radians(lon2 - lon1)
    a = math.sin(d_phi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(d_lambda / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))
