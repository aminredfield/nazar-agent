"""Does a new shift clash with a person's shift on another site? Counted by code.

Weekly schedules: a shift starts on each of its weekdays and may run past
midnight into the next day ("20:00–08:00", Sunday night into Monday). Two
shifts clash when their minutes of the week overlap. A schedule of another
kind (a cycle "two on, two off", chosen dates) is not guessed at: the caller
says it has to be checked by hand.

For a clash the way out is offered, never chosen: the new shift moved to
start when the other ends, or to end when it starts (same length, same days,
only if that clashes with nothing); the person moved off the other site; or
not added.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

DAY = 24 * 60
WEEK = 7 * DAY


@dataclass(frozen=True, slots=True)
class Shift:
    start: str  # HH:MM
    end: str  # HH:MM; at or before the start: the next morning
    days: tuple[int, ...]  # 1 = Monday … 7 = Sunday

    @property
    def minutes(self) -> int:
        return (_clock(self.end) - _clock(self.start)) % DAY or DAY

    def spans(self, day: int | None = None) -> list[tuple[int, int]]:
        """Its minutes of the week as [from, to), split where the week wraps; one weekday's only if given."""
        out: list[tuple[int, int]] = []
        for weekday in (day,) if day is not None else self.days:
            begin = (weekday - 1) * DAY + _clock(self.start)
            end = begin + self.minutes
            out.extend([(begin, min(end, WEEK))] + ([(0, end - WEEK)] if end > WEEK else []))
        return out


@dataclass(frozen=True, slots=True)
class Clash:
    weekdays: tuple[int, ...]  # the days of the new shift that clash
    window: tuple[str, str]  # when the first clash is, as HH:MM–HH:MM


@dataclass(frozen=True, slots=True)
class Option:
    kind: Literal["shift", "transfer", "skip"]
    shift: Shift | None = None  # for "shift": the new shift that clashes with nothing


def clash(new: Shift, other: Shift) -> Clash | None:
    theirs = other.spans()
    days, first = [], None
    for weekday in new.days:
        for begin, end in new.spans(weekday):
            for their_begin, their_end in theirs:
                low, high = max(begin, their_begin), min(end, their_end)
                if low < high:
                    days.append(weekday)
                    first = first or (low, high)
    if first is None:
        return None
    return Clash(tuple(sorted(set(days))), (_hhmm(first[0]), _hhmm(first[1])))


def options(new: Shift, others: list[Shift]) -> list[Option]:
    """Ways out when ``new`` clashes with any of ``others``; nothing if it clashes with none."""
    clashing = [other for other in others if clash(new, other)]
    if not clashing:
        return []
    moved = []
    after = max(_clock(other.start) + other.minutes for other in clashing) % DAY
    before = min(_clock(other.start) for other in clashing)
    for start in (after, (before - new.minutes) % DAY):
        candidate = replace(new, start=_hhmm(start), end=_hhmm(start + new.minutes))
        if candidate.minutes == new.minutes and not any(clash(candidate, other) for other in others):
            moved.append(Option("shift", candidate))
    unique = list({option.shift: option for option in moved}.values())
    return [*unique, Option("transfer"), Option("skip")]


def _clock(value: str) -> int:
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def _hhmm(minutes: int) -> str:
    minutes %= DAY
    return f"{minutes // 60:02d}:{minutes % 60:02d}"
