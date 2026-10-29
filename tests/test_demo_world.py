"""The demo company holds the cases the agents are judged on — and nothing that fakes a finding."""

from collections import Counter
from datetime import datetime, timedelta

import pytest

from nazar_agent.demo.photos import photo
from nazar_agent.demo.world import TZ, build_world, phone_key

# A Wednesday, mid-afternoon: today's check-ins are in, check-outs are not yet.
NOW = datetime(2026, 10, 28, 15, 30, tzinfo=TZ)


def by_name(world, name):
    return next(person for person in world.people.values() if person.name == name)


def events_of(world, name, day=None):
    person = by_name(world, name)
    return [
        event for event in world.events
        if event.employee_id == person.id and (day is None or event.at.astimezone(TZ).date() == day)
    ]


def test_the_same_seed_and_moment_give_the_same_world():
    first, second = build_world(NOW), build_world(NOW)

    assert [(e.id, e.at, e.device_key) for e in first.events] == [(e.id, e.at, e.device_key) for e in second.events]
    assert len(first.events) > 500


def test_today_has_check_ins_up_to_now_and_nothing_later():
    world = build_world(NOW)
    today = [event for event in world.events if event.at.astimezone(TZ).date() == NOW.date()]

    assert {event.event_type for event in today} == {"check_in"}  # the shift ends at 18:00
    assert len({event.employee_id for event in today}) >= 14
    assert max(event.at for event in world.events) <= NOW


def test_no_day_before_the_history_is_planned():
    """NAZAR's seed started assignments a day early: 16 people "missed" that day."""
    world = build_world(NOW)
    first_events = min(event.at for event in world.events).astimezone(TZ).date()

    assert world.first_day == first_events
    assert all(person.assigned_from == world.first_day for person in world.people.values())


def test_dead_souls_come_from_the_foremans_phone_as_photos_of_a_screen_after_they_left():
    world = build_world(NOW)
    foreman_phone = phone_key("Rustam Yuldashev")
    left_day = NOW.date() - timedelta(days=10)

    for name in ("Timur Hasanov", "Gulchehra Ibragimova", "Mohira Yunusova", "Feruza Karimova"):
        before = [event for event in events_of(world, name) if event.at.astimezone(TZ).date() < left_day]
        after = [event for event in events_of(world, name) if event.at.astimezone(TZ).date() >= left_day]
        assert before and {event.device_key for event in before} == {phone_key(name)}
        assert {event.photo_kind for event in before} == {"live"}
        assert after and {event.device_key for event in after} == {foreman_phone}
        assert {event.photo_kind for event in after} == {"screen"}


def test_the_honest_foreman_checks_in_his_crew_with_live_selfies_on_the_site():
    world = build_world(NOW)
    crew = events_of(world, "Hurshid Rahmonov") + events_of(world, "Anvar Bakirov")

    assert {event.device_key for event in crew} == {phone_key("Dilshod Mirzaev")}
    assert {event.photo_kind for event in crew} == {"live"} and all(event.inside for event in crew)


def test_weak_gps_is_flagged_and_twice_outside_the_fence_with_full_days():
    world = build_world(NOW)
    events = events_of(world, "Sherzod Alimov")
    outside_days = {event.at.astimezone(TZ).date() for event in events if not event.inside}

    assert all("gps_accuracy_warning" in event.risk_flags for event in events)
    assert len(outside_days) == 2 and {event.photo_kind for event in events} == {"live"}


def test_the_finishing_crew_comes_at_nine_against_a_schedule_of_eight():
    world = build_world(NOW)
    site = by_name(world, "Zuhra Nabieva").site_id
    arrivals = [event.at.astimezone(TZ) for event in events_of(world, "Zuhra Nabieva") if event.event_type == "check_in"]

    assert world.sites[site].schedule_on(NOW.date()).shift_start == "08:00"
    assert all(8 * 60 + 50 <= arrival.hour * 60 + arrival.minute <= 9 * 60 + 10 for arrival in arrivals)


def test_ordinary_absences_are_rare_and_spread_out():
    world = build_world(NOW)
    workers = [person for person in world.people.values() if person.kind == "worker"]
    shift_days = [
        day for day in (world.first_day + timedelta(days=n) for n in range((NOW.date() - world.first_day).days))
        if world.sites[workers[0].site_id].shift_on(day)
    ]
    missed = Counter(
        day.isoweekday()
        for person in workers for day in shift_days
        if not events_of(world, person.name, day)
    )

    assert sum(missed.values()) <= len(workers) * len(shift_days) * 0.1
    assert max(missed.values(), default=0) <= 3  # no weekday collects the absences


def test_photos_are_jpegs_and_the_same_recipe_gives_the_same_picture():
    world = build_world(NOW)
    event = next(event for event in world.events if event.photo_kind == "screen")

    first = photo(event.face, event.photo_kind, event.photo_seed)

    assert first[:3] == b"\xff\xd8\xff" and len(first) > 10_000
    assert photo(event.face, event.photo_kind, event.photo_seed) == first



@pytest.mark.parametrize("moment", ["2026-10-26T10:00", "2026-11-01T12:00", "2026-11-04T07:30", "2026-11-09T20:00"])
def test_the_cases_hold_on_any_day(moment):
    world = build_world(datetime.fromisoformat(moment).replace(tzinfo=TZ))
    weak = by_name(world, "Sherzod Alimov")
    souls_today = [event for event in world.events if event.device_key == phone_key("Rustam Yuldashev")]

    assert len({event.at.date() for event in world.events if event.employee_id == weak.id and not event.inside}) == 2
    assert {by_name(world, name).id for name in ("Timur Hasanov", "Feruza Karimova")} <= {e.employee_id for e in souls_today}
    assert max(event.at for event in world.events) <= world.now
