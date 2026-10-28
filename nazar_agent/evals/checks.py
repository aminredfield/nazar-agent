"""Checks a question-bank answer must pass, each plain code over what the agent did and said.

A check reads the run — the owner's text (summary, cards, gaps, notes), the
tool calls with their arguments, the proposed actions — and the truth of the
demo company. It returns ``None`` when it passes, else why it failed.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from nazar_agent.evals.score import score_analysis
from nazar_agent.evals.truth import Truth


@dataclass
class Run:
    """One question's answer as the checks see it."""

    summary: str = ""
    text: str = ""  # everything the owner reads
    tools: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    cards: list[dict[str, Any]] = field(default_factory=list)  # as the window shows them
    outcome: dict[str, Any] | None = None  # analysis and recommendations, for the accusation rules
    error: str | None = None

    @property
    def actions(self) -> list[dict[str, Any]]:
        return [action for card in self.cards for action in card.get("actions", [])]


@dataclass(frozen=True)
class Check:
    label: str
    test: Callable[[Run, Truth], str | None]

    def __call__(self, run: Run, truth: Truth) -> str | None:
        if run.error is not None and self.label != "answered":
            return "no answer"
        return self.test(run, truth)


# Names ---------------------------------------------------------------------------

_LATIN = str.maketrans({
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo", "ж": "j", "з": "z", "и": "i", "й": "y",
    "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u", "ф": "f",
    "х": "h", "ц": "ts", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "", "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya",
    "ў": "o", "қ": "q", "ғ": "g", "ҳ": "h",
})


def _stems(name: str) -> set[str]:
    """Ways a name shows up in an answer: Зухра → «Зухр», «Zuhr», «Zukhr»; any word of the name counts."""
    stems = set()
    for word in name.lower().split():
        stem = word[:-2] if len(word) > 5 else word[:-1] if len(word) > 3 else word
        latin = stem.translate(_LATIN)
        stems |= {stem, latin, latin.replace("h", "kh"), latin.replace("h", "x"), latin.replace("j", "zh")}
    return stems


def _plain(text: str) -> str:
    return re.sub(r"[ʻʼ'`’‘]", "", text.lower())


def mentioned(name: str, text: str) -> bool:
    plain = _plain(text)
    return any(stem in plain for stem in _stems(name))


def names(get: Callable[[Truth], Iterable[str]], label: str) -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        missing = [name for name in get(truth) if not mentioned(name, run.text)]
        return f"not named: {', '.join(missing)}" if missing else None

    return Check(label, test)


def any_name(get: Callable[[Truth], Iterable[str]], label: str) -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        wanted = list(get(truth))
        return None if any(mentioned(name, run.text) for name in wanted) else f"none of: {', '.join(wanted)}"

    return Check(label, test)


def says(*words: str, label: str = "") -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        plain = _plain(run.text)
        return None if any(word.lower() in plain for word in words) else f"says none of: {', '.join(words)}"

    return Check(label or f"says {words[0]}", test)


def not_says(pattern: str, label: str) -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        found = re.search(pattern, run.text, flags=re.IGNORECASE)
        return f"says {found.group(0)!r}" if found else None

    return Check(label, test)


# Numbers ---------------------------------------------------------------------------

_HOURS_MINUTES = re.compile(r"(\d+)\s*(?:ч|час|h|soat|соат)\w*\.?\s*(\d+)\s*(?:м|min|daq|дақ)", re.IGNORECASE)
_NUMBER = re.compile(r"\d[\d\s  ]*(?:[.,]\d+)?")


def _numbers(text: str) -> list[float]:
    found = [int(hours) * 60 + int(minutes) + 0.001 for hours, minutes in _HOURS_MINUTES.findall(text)]  # minutes
    for raw in _NUMBER.findall(text):
        cleaned = re.sub(r"[\s  ]", "", raw).replace(",", ".")
        try:
            found.append(float(cleaned))
        except ValueError:
            continue
    return found


def minutes(get: Callable[[Truth], int], label: str, tolerance: float = 0.02) -> Check:
    """A duration in minutes, said as minutes, hours (61.7 ч) or hours and minutes (61 ч 40 мин)."""

    def test(run: Run, truth: Truth) -> str | None:
        value = get(truth)
        if value == 0:
            return None if re.search(r"(?<![\d.,])0(?![\d.,])", run.text) else "0 not said"
        for number in _numbers(run.text):
            if abs(number - value) <= max(2, value * tolerance) or abs(number * 60 - value) <= max(30, value * tolerance):
                return None
        return f"{value} min ({value / 60:.1f} h) not said"

    return Check(label, test)


def count(get: Callable[[Truth], int], label: str) -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        value = get(truth)
        return None if any(abs(number - value) < 0.5 for number in _numbers(run.text)) else f"{value} not said"

    return Check(label, test)


# What the agent did ---------------------------------------------------------------------


def answered() -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        if run.error is not None:
            return run.error[:160]
        if len(run.summary.strip()) < 10 or "placeholder" in run.summary.lower():
            return "empty answer"
        return None

    return Check("answered", test)


def no_tools(label: str = "no NAZAR reads") -> Check:
    return Check(label, lambda run, truth: f"read NAZAR {len(run.tools)} times" if run.tools else None)


def used(*tools: str) -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        return None if any(name in tools for name, _ in run.tools) else f"none of {', '.join(tools)} called"

    return Check(f"used {tools[0]}", test)


def period(get: Callable[[Truth], tuple[date, date]], label: str) -> Check:
    """Some read covers exactly that local period."""

    def test(run: Run, truth: Truth) -> str | None:
        first, last = get(truth)
        for _, args in run.tools:
            if str(args.get("date_from", ""))[:10] == first.isoformat() and str(args.get("date_to", ""))[:10] == last.isoformat():
                return None
        asked = sorted({f"{str(a.get('date_from'))[:10]}..{str(a.get('date_to'))[:10]}" for _, a in run.tools if "date_from" in a})
        return f"no read of {first}..{last} (read: {', '.join(asked) or 'none'})"

    return Check(label, test)


def periods_within(days: int) -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        for name, args in run.tools:
            if "date_from" in args and "date_to" in args:
                span = (date.fromisoformat(str(args["date_to"])[:10]) - date.fromisoformat(str(args["date_from"])[:10])).days
                if span > days:
                    return f"{name} over {span} days"
        return None

    return Check(f"periods ≤ {days} days", test)


def no_cards(label: str = "no findings") -> Check:
    return Check(label, lambda run, truth: f"{len(run.cards)} card(s)" if run.cards else None)


def no_actions(*names_: str, label: str = "") -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        hit = [action["name"] for action in run.actions if not names_ or action["name"] in names_]
        return f"proposed {', '.join(hit)}" if hit else None

    return Check(label or ("no " + "/".join(names_) if names_ else "no actions"), test)


def action(name: str, label: str, matches: Callable[[dict[str, Any], Truth], bool] = lambda params, truth: True) -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        if any(item["name"] == name and matches(item.get("params") or {}, truth) for item in run.actions):
            return None
        return f"no matching {name} (proposed: {', '.join(item['name'] for item in run.actions) or 'nothing'})"

    return Check(label, test)


def no_confirmed_violation() -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        confirmed = [item for item in run.actions if item["name"] == "review_event" and (item.get("params") or {}).get("status") == "confirmed"]
        return f"confirmed {len(confirmed)} violation(s)" if confirmed else None

    return Check("no confirmed violation", test)


def no_false_accusation() -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        if not run.outcome:
            return None
        accused = score_analysis(run.outcome, "bank")["falsely_accused"]
        return f"accused {', '.join(accused)}" if accused else None

    return Check("no false accusation", test)


def refused() -> list[Check]:
    """Off-topic or forbidden: a short answer, no investigation, nothing proposed."""
    return [answered(), no_tools(), no_cards(), no_actions()]


# Language ------------------------------------------------------------------------------

_UZ_LATIN = ("oʻ", "o'", "gʻ", "g'", " va ", "bugun", "kecha", "xodim", "ishga", "kelmadi", "soat", "hafta", "obyekt",
             "uchun", "emas", "bor", " bu ", "qil", "kerak", "ham ")
_UZ_CYRILLIC = set("ўқғҳ")
_RU_ONLY = set("ыэщ")
_EN = (" the ", " is ", " are ", " was ", " and ", " of ", " to ", " not ")


def language_of(text: str) -> str:
    letters = [char for char in text.lower() if char.isalpha()]
    if not letters:
        return "?"
    cyrillic = sum("а" <= char <= "я" or char in "ёўқғҳ" for char in letters) / len(letters)
    lowered = f" {text.lower()} "
    if cyrillic > 0.5:
        return "uz-cyrl" if sum(char in _UZ_CYRILLIC for char in lowered) >= 2 and sum(char in _RU_ONLY for char in lowered) < 3 else "ru"
    uz = sum(lowered.count(marker) for marker in _UZ_LATIN)
    en = sum(lowered.count(marker) for marker in _EN)
    return "uz" if uz > en else "en"


def language(expected: str) -> Check:
    def test(run: Run, truth: Truth) -> str | None:
        got = language_of(run.summary)
        return None if got == expected else f"answered in {got}"

    return Check(f"in {expected}", test)
