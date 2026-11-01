"""What the agent may do after a scene, and the employee's cabinet the explain scene talks through.

The model gives a verdict; code picks the actions that verdict allows, from four — flag for
review, close with no violation, don't count the day, ask for a live selfie — and nothing
else. The visitor applies and undoes them on the scene's own record: the scenes are staged,
so nothing here touches NAZAR. Each visitor (the session cookie) has their own scenes; the
desk keeps the most recent visitors only.
"""

from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from nazar_agent.scenarios.stage import EXPLAIN, HEADCOUNT, PLACES

ACTIONS = ("flag_review", "close_fine", "void_day", "live_selfie")
PAYS = {"void_day"}  # changes what is paid: the window asks once more before it
MAX_VISITORS = 2000

# The page, the verdict → the actions. A scene of the explain page may differ: Owen did not come.
RULES: dict[str, dict[str, tuple[str, ...]]] = {
    "gps": {"same": ("close_fine",), "not_same": ("flag_review", "live_selfie")},
    "explain": {"consistent": ("close_fine",), "contradicts": ("flag_review",), "cannot_tell": ("flag_review",)},
    "headcount": {"all_here": ("close_fine",), "missing": ("live_selfie",), "extra": ("flag_review",)},
    "roster": {},
}
SCENE_RULES: dict[tuple[str, str, str], tuple[str, ...]] = {
    ("explain", "store-screen", "consistent"): ("void_day",),
}
SCENES: dict[str, dict[str, Any]] = {"gps": PLACES, "explain": EXPLAIN, "headcount": HEADCOUNT}


class ActRefused(Exception):
    """An action the scene's verdict does not allow, or one already done."""


def allowed(page: str, scene: str, verdict: str) -> tuple[str, ...]:
    return SCENE_RULES.get((page, scene, verdict), RULES.get(page, {}).get(verdict, ()))


@dataclass
class _Scene:
    verdict: str = ""
    subject: str = ""  # the GPS scene: which new check-in was judged
    applied: list[str] = field(default_factory=list)


@dataclass
class _Inbox:
    message: str = ""
    lang: str = "en"
    reply: str = ""
    check: dict[str, Any] | None = None


class SceneDesk:
    def __init__(self) -> None:
        self._visitors: OrderedDict[str, dict[tuple[str, str], Any]] = OrderedDict()

    def _of(self, visitor: str) -> dict[tuple[str, str], Any]:
        state = self._visitors.setdefault(visitor, {})
        self._visitors.move_to_end(visitor)
        while len(self._visitors) > MAX_VISITORS:
            self._visitors.popitem(last=False)
        return state

    def forget(self, visitor: str) -> None:
        self._visitors.pop(visitor, None)

    # ---- verdicts and actions ----------------------------------------------------------

    def judged(self, visitor: str, page: str, scene: str, verdict: str, subject: str = "") -> list[str]:
        """A new verdict starts the scene over: what was applied belonged to the one before."""
        self._of(visitor)[(page, scene)] = _Scene(verdict, subject)
        return list(allowed(page, scene, verdict))

    def applied(self, visitor: str, page: str, scene: str) -> list[str]:
        state = self._of(visitor).get((page, scene))
        return list(state.applied) if isinstance(state, _Scene) else []

    def act(
        self, visitor: str, page: str, scene: str, action: str, undo: bool = False, confirmed: bool = False
    ) -> dict[str, Any]:
        """``confirmed`` — the visitor said yes a second time: an action that changes pay needs it."""
        state = self._of(visitor).get((page, scene))
        if not isinstance(state, _Scene) or not state.verdict:
            raise ActRefused("Play the scene first: there is no verdict to act on.")
        if action not in allowed(page, scene, state.verdict):
            raise ActRefused("This action does not follow from the verdict.")
        if action in PAYS and not undo and not confirmed:
            raise ActRefused("This changes pay: confirm it once more.")
        if undo:
            if action not in state.applied:
                raise ActRefused("Nothing to undo.")
            state.applied.remove(action)
        else:
            if action in state.applied:
                raise ActRefused("Already done.")
            state.applied.append(action)
        return {"status": "undone" if undo else "applied", "effect": effect(page, scene, action, state.subject)}

    # ---- the employee's cabinet ------------------------------------------------------------

    def asked(self, visitor: str, scene: str, message: str, lang: str) -> None:
        self._of(visitor)[("inbox", scene)] = _Inbox(message, lang)

    def replied(self, visitor: str, scene: str, said: str, check: dict[str, Any]) -> None:
        inbox = self._of(visitor).get(("inbox", scene))
        if not isinstance(inbox, _Inbox) or not inbox.message:
            raise ActRefused("The employee has no question yet.")
        inbox.reply, inbox.check = said, check

    def inbox(self, visitor: str, scene: str) -> dict[str, Any]:
        found = self._of(visitor).get(("inbox", scene))
        inbox = found if isinstance(found, _Inbox) else _Inbox()
        return {"message": inbox.message, "lang": inbox.lang, "reply": inbox.reply, "check": inbox.check}


def effect(page: str, scene: str, action: str, subject: str = "") -> dict[str, Any]:
    """What the action changes, as codes the window puts in words: the record, the field, before → after."""
    case = SCENES[page][scene]
    if page == "gps":
        who = case["candidates"][int(subject or 0)]["who"]
        record = case["place"]
    elif page == "explain":
        who, record = case["employee"], case["record"]
    else:
        who, record = "", case["place"]
    change: dict[str, Any] = {
        "flag_review": {"field": "review", "before": "not_reviewed", "after": "flagged"},
        "close_fine": {"field": "review", "before": "not_reviewed", "after": "no_violation"},
        "void_day": {"field": "paid", "before": case.get("paid_minutes", 0), "after": 0},
        "live_selfie": {"field": "selfie", "before": "none", "after": "requested"},
    }[action]
    count = len(case["checked_in"]) if page == "headcount" else 1
    return {"action": action, "who": who, "record": record, "count": count, "pays": action in PAYS, **change}
