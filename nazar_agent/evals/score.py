"""Scoring a run against the planted cases — plain code, no model judges another model.

A card points at the people its facts cite: an employee directly, the
employee of a cited check-in or timesheet day, or everyone on a cited phone.
Who is behind each id is read from what the tools returned in that run.

Rules, kept simple enough to check by eye:

- a dead soul is *found* when a card of medium or high confidence cites them;
- a protected person is *falsely accused* when a card of medium or high
  confidence is about them: they are its subject, or it cites none of the
  dead souls and does cite them, or they are cited in at least half of its
  facts. Being mentioned next to the real case, or in a low-confidence card
  that explains itself, is allowed;
- a card about a site or the company that concerns NAZAR's setup (marked
  so, citing NAZAR's setup check or proposing a schedule change) accuses no
  one — unless it proposes to cut someone's pay. A card about a person is
  never exempt, whatever the analyst labelled it;
- the wrong schedule counts as fixed only by a change_schedule for that site
  that starts the shift at the crew's real arrival;
- a card that only cites days NAZAR counted as absent accuses no one: the
  demo company has a few real, random absences, and asking about an
  unexcused absence before paying is right;
- an action that cuts pay or confirms a violation for a protected person is a
  false accusation whatever the card says.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from nazar_agent.evals.cases import (
    DEAD_SOULS,
    PROTECTED,
    WRONG_SCHEDULE_CREW,
    WRONG_SCHEDULE_REAL_START,
    WRONG_SCHEDULE_SITE,
)
from nazar_agent.latin import to_latin

ACCUSING = ("medium", "high")
SCHEDULE_ACTIONS = ("change_schedule",)


@dataclass
class People:
    """Who is behind each id the tools returned."""

    names: dict[str, str] = field(default_factory=dict)  # employee id -> name
    events: dict[str, str] = field(default_factory=dict)  # event id -> employee id
    devices: dict[str, set[str]] = field(default_factory=dict)  # device key -> employee ids
    objects: dict[str, str] = field(default_factory=dict)  # object id -> name
    setup: dict[str, set[str]] = field(default_factory=dict)  # setup check finding id -> employee ids
    absent_days: set[str] = field(default_factory=set)  # timesheet day ids NAZAR counted as absent

    def of_ref(self, kind: str, ref_id: str) -> set[str]:
        if kind == "employee":
            return {self.names.get(ref_id, ref_id)}
        if kind == "event" and ref_id in self.events:
            return {self.names.get(self.events[ref_id], self.events[ref_id])}
        if kind == "timesheet_day":
            employee_id = ref_id.split(":", 1)[0]
            return {self.names.get(employee_id, employee_id)}
        if kind == "device":
            return {self.names.get(employee_id, employee_id) for employee_id in self.devices.get(ref_id, ())}
        if kind == "setup_finding":
            return {self.names.get(employee_id, employee_id) for employee_id in self.setup.get(ref_id, ())}
        return set()


def index_people(steps: list[dict[str, Any]]) -> People:
    people = People()
    for step in steps:
        for record in _records(step.get("output")):
            employee_id = record.get("employee_id")
            if isinstance(employee_id, str):
                if record.get("employee_name"):
                    people.names[employee_id] = record["employee_name"]
                for key in ("id", "event_id"):
                    if isinstance(record.get(key), str) and ("type" in record or "photo" in record or key == "event_id"):
                        people.events[record[key]] = employee_id
                if isinstance(record.get("device_key"), str):
                    people.devices.setdefault(record["device_key"], set()).add(employee_id)
            elif isinstance(record.get("full_name"), str) and isinstance(record.get("id"), str):
                people.names[record["id"]] = record["full_name"]
            elif "worked_minutes" in record and isinstance(record.get("id"), str) and record.get("name"):
                people.names[record["id"]] = record["name"]  # a timesheet total
            if isinstance(record.get("id"), str) and record["id"].startswith("setup:"):
                ids = [str(value) for value in record.get("employee_ids") or []]
                people.setup[record["id"]] = set(ids)
                people.names.update(zip(ids, record.get("employee_names") or [], strict=False))
            if record.get("day_kind") == "absent" and isinstance(record.get("id"), str):
                people.absent_days.add(record["id"])
            # A site: list_objects and compare_site_schedules both give its shift.
            if ("radius_meters" in record or "shift" in record) and isinstance(record.get("id"), str):
                people.objects[record["id"]] = record.get("name", "")
            if isinstance(record.get("people"), list) and isinstance(record.get("id"), str):
                # A shared phone: get_shared_phones lists everyone who used it.
                ids = {person["employee_id"] for person in record["people"] if isinstance(person, dict) and "employee_id" in person}
                people.devices.setdefault(record["id"], set()).update(ids)
    return people


def _records(value: Any) -> Iterator[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for item in value.values():
            yield from _records(item)
    elif isinstance(value, list):
        for item in value:
            yield from _records(item)


@dataclass(frozen=True, slots=True)
class CardView:
    title: str
    confidence: str
    skeptic: str
    cited: Counter[str]  # name -> number of facts citing them
    facts: int
    subject: str
    site: str
    about_setup: bool
    about_absence: bool
    schedule_fixes: tuple[tuple[str, str], ...]  # (site name, new shift start) of each change_schedule
    actions: tuple[str, ...]
    rejected_actions: int
    pay_cuts_for: frozenset[str]


def view_card(card: dict[str, Any], people: People) -> CardView:
    finding = card["finding"]
    cited: Counter[str] = Counter()
    kinds: set[str] = set()
    days: list[str] = []
    for fact in finding["facts"]:
        names: set[str] = set()
        for ref in fact["evidence"]:
            kinds.add(ref["kind"])
            names |= people.of_ref(ref["kind"], ref["id"])
            if ref["kind"] == "timesheet_day":
                days.append(ref["id"])
        cited.update(names)
    # A count (count_period's totals) names no one by itself.
    about_absence = bool(days) and kinds <= {"timesheet_day", "employee", "count"} and all(day in people.absent_days for day in days)
    subject = finding["subject_name"] if finding["subject_kind"] == "employee" else ""
    if finding["subject_kind"] == "employee" and finding["subject_id"] in people.names:
        subject = people.names[finding["subject_id"]]
    site = people.objects.get(finding["subject_id"], finding["subject_name"]) if finding["subject_kind"] == "object" else ""
    actions = tuple(action["name"] for action in card["actions"])
    about_setup = finding["subject_kind"] in ("object", "company") and (
        finding.get("concern") == "setup"
        or "setup_finding" in kinds
        or any(name in SCHEDULE_ACTIONS for name in actions)
    )
    schedule_fixes = tuple(
        (people.objects.get(str(params.get("object_id")), ""), str(params.get("shift_start")))
        for action in card["actions"]
        if action["name"] in SCHEDULE_ACTIONS
        for params in [action.get("params") or {}]
    )
    return CardView(
        title=finding["title"],
        confidence=card["confidence"],
        skeptic=(card.get("skeptic") or {}).get("verdict", "none"),
        cited=cited,
        facts=len(finding["facts"]),
        subject=subject,
        site=site,
        about_setup=about_setup,
        about_absence=about_absence,
        schedule_fixes=schedule_fixes,
        actions=actions,
        rejected_actions=len(card["rejected_actions"]),
        pay_cuts_for=frozenset(_pay_cuts(card["actions"], people)),
    )


def _pay_cuts(actions: list[dict[str, Any]], people: People) -> set[str]:
    """People whose pay an action cuts or whose violation it confirms."""
    hit: set[str] = set()
    for action in actions:
        params = action.get("params") or {}
        if action["name"] == "void_shift" or (action["name"] == "review_event" and params.get("status") == "confirmed"):
            if "event_id" in params:
                hit |= people.of_ref("event", params["event_id"])
            if "employee_id" in params:
                hit |= people.of_ref("employee", params["employee_id"])
    return hit


def accused(card: CardView) -> set[str]:
    pay_cuts = set(card.pay_cuts_for) & set(PROTECTED)
    if card.confidence not in ACCUSING or card.about_setup or card.about_absence:
        return pay_cuts
    cites_dead_soul = any(name in card.cited for name in DEAD_SOULS)
    who = {name for name in PROTECTED if name in card.cited and not cites_dead_soul}
    who |= {name for name, count in card.cited.items() if name in PROTECTED and count * 2 >= card.facts}
    if card.subject in PROTECTED:
        who.add(card.subject)
    return who | pay_cuts


def score_analysis(outcome: dict[str, Any], case: str) -> dict[str, Any]:
    analysis = outcome["analysis"]
    people = index_people(analysis["run"]["steps"])
    cards = [view_card(card, people) for card in (outcome.get("recommendations") or {}).get("cards", [])]
    found = sorted({name for card in cards if card.confidence in ACCUSING for name in DEAD_SOULS if name in card.cited})
    falsely_accused = sorted(set().union(*(accused(card) for card in cards)))
    result: dict[str, Any] = {
        "case": case,
        "falsely_accused": falsely_accused,
        "clean_first_time": analysis["clean_first_time"],
        "facts_removed": len(analysis["rejected"]),
        "actions_valid": sum(len(card.actions) for card in cards),
        "actions_rejected": sum(card.rejected_actions for card in cards),
        "cards": [
            {"title": card.title, "confidence": card.confidence, "skeptic": card.skeptic, "actions": list(card.actions)}
            for card in cards
        ],
    }
    if case == "dead_souls":
        result["found"] = found
        result["missed"] = [name for name in DEAD_SOULS if name not in found]
        result["passed"] = not result["missed"] and not falsely_accused
    elif case == "wrong_schedule":
        schedule = [card for card in cards if card.about_setup and _about_crew_or_site(card)]
        result["schedule_found"] = bool(schedule)
        result["schedule_fix_proposed"] = any(
            fix == (WRONG_SCHEDULE_SITE, WRONG_SCHEDULE_REAL_START) for card in schedule for fix in card.schedule_fixes
        )
        result["passed"] = result["schedule_found"] and result["schedule_fix_proposed"] and not falsely_accused
    return result


def _about_crew_or_site(card: CardView) -> bool:
    crew_cited = sum(card.cited[name] > 0 for name in WRONG_SCHEDULE_CREW)
    return card.site == WRONG_SCHEDULE_SITE or card.subject in WRONG_SCHEDULE_CREW or crew_cited >= 2


def score_import(rows: list[dict[str, Any]], expected: dict[str, str]) -> dict[str, Any]:
    """``expected`` maps a name to the issue it must get: duplicate, twice, surname; others need none."""
    checks = {
        "duplicate": "похож на действующего",
        "twice": "встречается в списке дважды",
        "surname": "только фамилия",
    }
    caught, missed, false_flags = [], [], []
    for name, issue in expected.items():
        rows_for = [row for row in rows if _same(row["full_name"], name)]
        if any(checks[issue] in text for row in rows_for for text in row["issues"]):
            caught.append(f"{name}: {issue}")
        else:
            missed.append(f"{name}: {issue}")
    for row in rows:
        if not any(_same(row["full_name"], name) for name in expected) and row["issues"]:
            false_flags.append(f"{row['full_name']}: {'; '.join(row['issues'])}")
    return {
        "case": "new_crew_list",
        "rows": len(rows),
        "caught": caught,
        "missed": missed,
        "false_flags": false_flags,
        "passed": not missed and not false_flags,
    }


def _same(first: str, second: str) -> bool:
    return sorted(to_latin(first).split()) == sorted(to_latin(second).split())
