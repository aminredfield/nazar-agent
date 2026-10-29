"""Names the owner wrote, checked by code before anyone investigates.

"Что с Каримовым?" in a company with two Karimovs is a question to ask back,
not to answer about one of them; a name no one has is answered at once. Each
choice is a question to send again, with the full name, as a button.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Any

from nazar_agent.engine import ToolExecutionError
from nazar_agent.nazar import NazarClient
from nazar_agent.roles.dispatcher import Request
from nazar_agent.tools.common import call_nazar
from nazar_agent.tools.facts import find, similarity

MAX_CHOICES = 4
NEAR = 0.6  # how close a name must be to be offered when no one matches
log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Choice:
    label: str
    question: str  # sent as is when the button is pressed


@dataclass(frozen=True, slots=True)
class Clarification:
    """Ask back instead of answering: which of these, or no one by that name."""

    name: str
    found: bool  # several matches; False — none
    choices: tuple[Choice, ...]


async def resolve(request: Request, question: str, nazar: NazarClient) -> Request | Clarification:
    """The request with the people it names found in NAZAR, or what to ask the owner first."""
    if not request.people:
        return request
    try:
        employees = await call_nazar(nazar.get("/employees"))
        objects = await call_nazar(nazar.get("/objects", {"include_inactive": True}))
    except ToolExecutionError as exc:  # the analyst will look them up itself
        log.warning("names not checked: %s", exc)
        return request
    if _named_in_full(question, employees):
        return request  # a choice already pressed: the full name is in the question

    found = []
    for name in request.people:
        # Sites too: the dispatcher may take "Яшнабад" for a person.
        result = find(name, employees, objects)
        matches = result["matches"]
        if matches and matches[0]["kind"] == "object":
            continue
        close = [match["name"] for match in matches if matches[0]["match"] - match["match"] < 0.05 and match["kind"] == "employee"]
        if len(close) > 1:
            return Clarification(name, True, _choices(question, close))
        if matches:
            found.append(f"{name} → {matches[0]['name']} (employee id {matches[0]['id']})")
        elif name[:1].isupper() or any(char.isdigit() for char in name):
            return Clarification(name, False, _choices(question, _near(name, employees)))
    if not found:
        return request
    return replace(request, resolved=tuple(found))


def _named_in_full(question: str, employees: list[dict[str, Any]]) -> bool:
    text = question.casefold()
    return any((person.get("full_name") or "").casefold() in text for person in employees if person.get("full_name"))


def _near(name: str, employees: list[dict[str, Any]]) -> list[str]:
    names = [person.get("full_name") or "" for person in employees if person.get("is_active", True)]
    scored = sorted(((similarity(name, other), other) for other in names if other), reverse=True)
    return [other for score, other in scored[:MAX_CHOICES] if score >= NEAR]


def _choices(question: str, names: list[str]) -> tuple[Choice, ...]:
    return tuple(Choice(label=name, question=f"{question.rstrip()} — {name}") for name in names[:MAX_CHOICES])
