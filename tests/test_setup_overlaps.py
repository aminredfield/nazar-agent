"""Shifts on two sites clash or not, counted by code, with ways out that clash with nothing."""

from nazar_agent.setup.overlaps import Clash, Option, Shift, clash, options

WEEKDAYS = (1, 2, 3, 4, 5)


def test_shifts_on_different_hours_or_days_do_not_clash():
    assert clash(Shift("08:00", "13:00", WEEKDAYS), Shift("14:00", "18:00", WEEKDAYS)) is None
    assert clash(Shift("08:00", "17:00", (6,)), Shift("08:00", "17:00", WEEKDAYS)) is None
    assert clash(Shift("08:00", "13:00", WEEKDAYS), Shift("13:00", "18:00", WEEKDAYS)) is None  # one ends as the other starts


def test_overlapping_hours_clash_on_the_shared_days():
    found = clash(Shift("08:00", "17:00", (1, 2, 3, 4, 5, 6)), Shift("14:00", "22:00", (5, 6, 7)))

    assert found == Clash(weekdays=(5, 6), window=("14:00", "17:00"))


def test_a_night_shift_clashes_with_the_next_mornings_shift():
    night = Shift("20:00", "08:00", (3,))  # Wednesday night into Thursday
    morning = Shift("07:00", "16:00", (4,))

    assert night.minutes == 720
    assert clash(night, morning) == Clash(weekdays=(3,), window=("07:00", "08:00"))
    assert clash(morning, night) == Clash(weekdays=(4,), window=("07:00", "08:00"))


def test_sunday_night_runs_into_monday():
    assert clash(Shift("22:00", "06:00", (7,)), Shift("05:00", "09:00", (1,))) == Clash((7,), ("05:00", "06:00"))


def test_a_clash_offers_a_shift_after_or_before_the_other_and_never_one_that_clashes():
    new = Shift("08:00", "17:00", WEEKDAYS)  # 9 hours
    there = Shift("06:00", "12:00", WEEKDAYS)

    found = options(new, [there])

    assert found[0] == Option("shift", Shift("12:00", "21:00", WEEKDAYS))
    assert found[1] == Option("shift", Shift("21:00", "06:00", WEEKDAYS))  # ends as the other starts, overnight
    assert [option.kind for option in found[2:]] == ["transfer", "skip"]
    assert all(clash(option.shift, there) is None for option in found if option.shift)


def test_no_way_to_move_when_the_day_is_full():
    found = options(Shift("08:00", "20:00", WEEKDAYS), [Shift("06:00", "14:00", WEEKDAYS), Shift("14:00", "02:00", WEEKDAYS)])

    assert [option.kind for option in found] == ["transfer", "skip"]


def test_no_clash_no_options():
    assert options(Shift("08:00", "13:00", WEEKDAYS), [Shift("14:00", "18:00", WEEKDAYS)]) == []
