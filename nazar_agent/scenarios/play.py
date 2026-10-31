"""Four abilities the Scenarios page shows, each one model call and code around it.

- ask / check — the employee explains: the agent writes them a question about a
  finding, then checks their answer against the records;
- read_roster — a company from one picture of the owner's schedule;
- count_shift — the people in a photo of the shift against who checked in;
- same_place — a check-in's background against the place's earlier selfies.

They take data, not the page's scenes, so the real assistant can call them too.
The model only reads and compares; code builds the plan, counts and wording.
"""

from __future__ import annotations

import base64
import json
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Literal

from nazar_agent.llm import LLMClient
from nazar_agent.recommendations.wording import WEEKDAYS
from nazar_agent.usage import count

ROLE = "scenario"
LANG_NAMES = {"en": "English", "ru": "Russian", "uz": "Uzbek (Latin letters)"}
Verdict = Literal["consistent", "contradicts", "cannot_tell"]


class ScenarioError(Exception):
    """The model's answer could not be read; the message is for the person."""


async def _json(llm: LLMClient, model: str, instructions: str, content: list[dict[str, Any]] | str,
                name: str, schema: dict[str, Any]) -> dict[str, Any]:
    turn = await llm.respond(
        model=model, instructions=instructions,
        input=content if isinstance(content, str) else [{"role": "user", "content": content}],
        tools=[], text_format={"type": "json_schema", "name": name, "strict": True, "schema": schema}, role=ROLE,
    )
    count(ROLE, model, turn.input_tokens, turn.output_tokens)
    try:
        return json.loads(turn.text)
    except json.JSONDecodeError as exc:
        raise ScenarioError("The model's answer could not be read — try again.") from exc


def _strict(properties: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": False, "required": list(properties), "properties": properties}


def _image(data: bytes, mime: str = "image/jpeg") -> dict[str, Any]:
    return {"type": "input_image", "image_url": f"data:{mime};base64,{base64.b64encode(data).decode()}"}


# --- The employee explains -------------------------------------------------------

ASK = """You write to an employee on behalf of their employer's time-and-attendance assistant.
Something in their records needs their side of the story. Write one short, polite message:
greet them by first name, say plainly what the records show (dates and times), and ask what
happened. No accusation, no threat, no legal words. Say they can answer by text or voice.
Write in {language}. Return JSON {{"message": "..."}}."""

CHECK = """You check what an employee says against their time-and-attendance records.
verdict: consistent — the records agree with what they say; contradicts — a record shows
otherwise; cannot_tell — the records neither confirm nor deny it. Judge the claim, never the
person. why: one sentence citing the record that decides it. next_step: one sentence for the
owner — what would settle it or what to do now. Write why and next_step in {language}, whatever
language the employee wrote in."""


async def ask(llm: LLMClient, model: str, case: dict[str, Any], lang: str) -> str:
    content = f"Employee: {case['employee']}\nWorkplace: {case['place']}\nWhat the assistant noticed: {case['finding']}\n" \
              "Records:\n" + "\n".join(f"- {line}" for line in case["records"])
    answer = await _json(llm, model, ASK.format(language=LANG_NAMES.get(lang, "English")), content,
                         "message", _strict({"message": {"type": "string"}}))
    return answer["message"].strip()


async def check(llm: LLMClient, model: str, case: dict[str, Any], said: str, lang: str) -> dict[str, str]:
    content = "Records:\n" + "\n".join(f"- {line}" for line in case["records"]) + f"\n\nThe employee says: {said.strip()}"
    return await _json(llm, model, CHECK.format(language=LANG_NAMES.get(lang, "English")), content, "check", _strict({
        "verdict": {"type": "string", "enum": ["consistent", "contradicts", "cannot_tell"]},
        "why": {"type": "string"},
        "next_step": {"type": "string"},
    }))


# --- A company from one picture ----------------------------------------------------

ROSTER = """Read an owner's staff schedule from this picture: a table, a screenshot of a chat,
a photo of a sheet. For each person: their name as written, their workplace (store, café,
office, site, client address), the days they work (1 = Monday … 7 = Sunday) and the shift
start and end (HH:MM, 24 hours). Write names in Latin letters as written. workplace: as
written, or "" when the picture does not say where they work — never a guess. unclear: anything
you could not read or had to guess, in {language}; empty when everything was clear."""


@dataclass(frozen=True, slots=True)
class Roster:
    people: list[dict[str, Any]]
    plan: list[str]
    unclear: list[str]


async def read_roster(llm: LLMClient, model: str, picture: bytes, mime: str, lang: str = "en") -> Roster:
    instructions = ROSTER.format(language=LANG_NAMES.get(lang, "English"))
    found = await _json(llm, model, instructions, [_image(picture, mime)], "roster", _strict({
        "people": {"type": "array", "items": _strict({
            "name": {"type": "string"}, "workplace": {"type": "string"},
            "days": {"type": "array", "items": {"type": "integer"}},
            "shift_start": {"type": "string"}, "shift_end": {"type": "string"},
        })},
        "unclear": {"type": "array", "items": {"type": "string"}},
    }))
    return Roster(found["people"], roster_plan(found["people"]), found["unclear"])


def roster_plan(people: list[dict[str, Any]]) -> list[str]:
    """The plan in words, by code: a location per workplace, a shift per group working it alike."""
    places: dict[str, dict[tuple[str, str, tuple[int, ...]], list[str]]] = defaultdict(lambda: defaultdict(list))
    nowhere = [person["name"].strip() for person in people if not person["workplace"].strip()]
    for person in people:
        if not person["workplace"].strip():
            continue
        shift = (person["shift_start"], person["shift_end"], tuple(sorted(set(person["days"]))))
        places[person["workplace"].strip()][shift].append(person["name"].strip())
    plan = []
    for place, shifts in places.items():
        plan.append(f"Create the location “{place}”.")
        for (start, end, days), names in shifts.items():
            plan.append(f"Shift {start}–{end}, {_days(days)}: {', '.join(names)}.")
    if nowhere:
        plan.append(f"Ask where they work — the picture does not say: {', '.join(nowhere)}.")
    plan.append(f"Add {len(people)} people — each waits for a face photo before NAZAR creates them.")
    return plan


def _days(days: tuple[int, ...]) -> str:
    days = tuple(day for day in days if 1 <= day <= 7)
    names = WEEKDAYS["en"]  # Monday first
    if len(days) > 2 and list(days) == list(range(days[0], days[-1] + 1)):
        return f"{names[days[0] - 1]}–{names[days[-1] - 1]}"
    return ", ".join(names[day - 1] for day in days)


# --- A photo of the shift ------------------------------------------------------------

COUNT = """Count the people in this photo of a team at the start of a shift. people: everyone you
can see, including anyone partly hidden or far in the background. in_team: those who are clearly
part of the team — the same work clothes or doing the work; visitors and passers-by are not.
notes: one sentence on anyone you were unsure about, in {language}; empty when nobody."""


async def count_shift(
    llm: LLMClient, model: str, photo: bytes, mime: str, checked_in: list[str], lang: str = "en"
) -> dict[str, Any]:
    instructions = COUNT.format(language=LANG_NAMES.get(lang, "English"))
    seen = await _json(llm, model, instructions, [_image(photo, mime)], "count", _strict({
        "people": {"type": "integer"}, "in_team": {"type": "integer"}, "notes": {"type": "string"},
    }))
    expected = len(checked_in)
    in_team = max(0, int(seen["in_team"]))
    if in_team == expected:
        verdict = f"Everyone who checked in is in the photo: {in_team} of {expected}."
    elif in_team < expected:
        verdict = (f"{expected} checked in, {in_team} in the photo: {expected - in_team} "
                   f"{'is' if expected - in_team == 1 else 'are'} not there — worth asking who.")
    else:
        verdict = f"{in_team} in the photo but only {expected} checked in: someone is working without a check-in."
    return {**seen, "in_team": in_team, "checked_in": expected, "verdict": verdict}


# --- The place behind a check-in ----------------------------------------------------

PLACE = """The first photos are earlier check-in selfies at one workplace. The last photo is a new
check-in whose GPS says it was taken there. Ignore the people; compare only the place behind them
— walls, furniture, shelves, counters, windows, floor. same_place: whether it is that workplace.
why: one sentence naming what in the background matches or differs, in {language}."""


async def same_place(
    llm: LLMClient, model: str, references: list[bytes], candidate: bytes, lang: str = "en"
) -> dict[str, Any]:
    content: list[dict[str, Any]] = []
    for reference in references:
        content += [{"type": "input_text", "text": "Earlier check-in:"}, _image(reference)]
    content += [{"type": "input_text", "text": "New check-in:"}, _image(candidate)]
    return await _json(llm, model, PLACE.format(language=LANG_NAMES.get(lang, "English")), content, "place", _strict({
        "same_place": {"type": "boolean"}, "why": {"type": "string"},
    }))
