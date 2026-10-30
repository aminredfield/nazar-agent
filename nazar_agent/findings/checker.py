"""The fact checker: plain code that verifies a report against the tool outputs.

It cannot be talked into anything. It knows only what the tools returned in
this run (the trace) and checks that

- every fact cites at least one record, and every cited id was actually seen;
- every claim names a record that was returned, a field that record has, and
  the value the tool gave for it;
- every finding is about a subject that exists and offers at least one
  innocent explanation;
- the direct answer's facts pass the same checks as a finding's;
- every number in the summary is one the tools returned — as is, or minutes
  said as hours — so a model's own sum or guess cannot reach the owner there.

Records are dicts with an ``id`` in any tool output; the same id seen in a
list and in a detail view is merged. Any other ``*_id`` value counts as a
reference that exists, but a claim needs the record itself.
"""

from __future__ import annotations

import contextlib
import json
import re
from dataclasses import dataclass
from typing import Any, Literal

from nazar_agent.engine import ToolStep
from nazar_agent.findings.models import AnalystReport, Claim, Fact, Finding

IssueCode = Literal[
    "no_evidence",
    "unknown_ref",
    "unknown_record",
    "missing_field",
    "value_mismatch",
    "unknown_subject",
    "no_alternatives",
    "no_facts",
    "unsupported_number",
]
Section = Literal["finding", "answer", "summary"]

# Counts (minutes, days) are integers and must match exactly. Measurements
# (distances, scores) are floats and agree within 1%: a model may write 6600
# for 6600.0 or round 6587.3 to 6590, but 6.6 for 6600.0 is a different number.
_RELATIVE_TOLERANCE = 0.01
_ABSOLUTE_TOLERANCE = 0.5
# A claimed text may be part of the tool's text ("10:14" of a timestamp, a
# first name of a full name), but not a one- or two-letter fragment.
_MIN_PARTIAL_TEXT = 4
# A number as written: 1824, 1 824, 30,4, 09:02, 28.10.2026.
_NUMBER = re.compile(r"\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?![\d.,:])|\d+(?:[:.,]\d+)*")
_MINUTES_AS_HOURS_FROM = 60


@dataclass(frozen=True, slots=True)
class Issue:
    finding: int  # 0-based index in the report; 0 for the answer and the summary
    fact: int | None  # None when the issue is about the finding itself
    code: IssueCode
    detail: str
    section: Section = "finding"

    def describe(self) -> str:
        if self.section == "summary":
            return f"summary: {self.detail}"
        where = "answer" if self.section == "answer" else f"finding {self.finding + 1}"
        if self.fact is not None:
            where += f", fact {self.fact + 1}"
        return f"{where}: {self.detail}"


@dataclass(frozen=True, slots=True)
class Evidence:
    """Everything the tools returned in one run, indexed for lookups."""

    records: dict[str, dict[str, Any]]
    seen_ids: frozenset[str]
    numbers: frozenset[str] = frozenset()  # every number the tools returned, as a summary may say it

    @classmethod
    def from_steps(cls, steps: tuple[ToolStep, ...]) -> Evidence:
        records: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        numbers: set[str] = set()
        for step in steps:
            if step.error is None:
                _index(step.output, records, seen)
                _collect_numbers(step.output, numbers)
        return cls(records=records, seen_ids=frozenset(seen | set(records)), numbers=frozenset(numbers))


def check_report(report: AnalystReport, evidence: Evidence) -> list[Issue]:
    issues: list[Issue] = []
    for index, fact in enumerate(report.answer):
        issues.extend(_check_fact(0, index, fact, evidence, "answer"))
    for index, finding in enumerate(report.findings):
        issues.extend(_check_finding(index, finding, evidence))
    unsupported = unsupported_numbers(report.summary, evidence)
    if unsupported:
        issues.append(Issue(
            0, None, "unsupported_number",
            f"it says {', '.join(unsupported)}, which no tool returned; take numbers from the facts or leave them out",
            "summary",
        ))
    return issues


def unsupported_numbers(text: str, evidence: Evidence) -> list[str]:
    """Numbers in a text that the tools never returned, in any form a summary may use."""
    return list(dict.fromkeys(token for token in _NUMBER.findall(text) if not _supported(token, evidence.numbers)))


def _check_finding(index: int, finding: Finding, evidence: Evidence) -> list[Issue]:
    issues: list[Issue] = []
    if finding.subject_kind != "company" and finding.subject_id not in evidence.seen_ids:
        issues.append(Issue(index, None, "unknown_subject", f"subject id {finding.subject_id!r} was not returned by any tool"))
    if not any(text.strip() for text in finding.alternatives):
        issues.append(Issue(index, None, "no_alternatives", "give at least one innocent explanation"))
    if not finding.facts:
        issues.append(Issue(index, None, "no_facts", "a finding needs at least one fact"))

    for fact_index, fact in enumerate(finding.facts):
        issues.extend(_check_fact(index, fact_index, fact, evidence, "finding"))
    return issues


def _check_fact(index: int, fact_index: int, fact: Fact, evidence: Evidence, section: Section) -> list[Issue]:
    issues: list[Issue] = []
    if not fact.evidence:
        issues.append(Issue(index, fact_index, "no_evidence", "the fact cites no record", section))
    for ref in fact.evidence:
        if ref.id not in evidence.seen_ids:
            issues.append(Issue(index, fact_index, "unknown_ref", f"{ref.kind}:{ref.id} was not returned by any tool", section))
    for claim in fact.claims:
        problem = _check_claim(claim, evidence)
        if problem is not None:
            issues.append(Issue(index, fact_index, problem[0], problem[1], section))
    return issues


def _check_claim(claim: Claim, evidence: Evidence) -> tuple[IssueCode, str] | None:
    record = evidence.records.get(claim.ref.id)
    if record is None:
        return "unknown_record", f"no tool returned the record {claim.ref.kind}:{claim.ref.id}"
    found, actual = _lookup(record, claim.field)
    if not found:
        return "missing_field", f"{claim.ref.kind}:{claim.ref.id} has no field {claim.field!r}"
    if not values_match(claim.value, actual):
        return "value_mismatch", f"{claim.ref.kind}:{claim.ref.id}.{claim.field} is {actual!r}, not {claim.value!r}"
    return None


def values_match(claimed: str, actual: Any) -> bool:
    text = claimed.strip()
    if isinstance(actual, list):
        return any(values_match(text, item) for item in actual) or text == str(actual) or _same_list(text, actual)
    if isinstance(actual, dict):
        # A flag is {"code", "meaning"}: claiming either one is fine.
        return any(values_match(text, actual[key]) for key in ("code", "meaning", "name") if key in actual)
    if isinstance(actual, bool):
        return text.lower() == str(actual).lower()
    if actual is None:
        return text.lower() in {"null", "none", ""}
    if isinstance(actual, (int, float)):
        number = _as_number(text)
        if number is None:
            return False
        if isinstance(actual, int):
            return number == actual
        return abs(number - actual) <= max(_RELATIVE_TOLERANCE * abs(actual), _ABSOLUTE_TOLERANCE)

    actual_text = str(actual).strip().casefold()
    claimed_text = text.casefold()
    if claimed_text == actual_text:
        return True
    return len(claimed_text) >= _MIN_PARTIAL_TEXT and claimed_text in actual_text


def _same_list(text: str, actual: list[Any]) -> bool:
    """The whole list written another way: '["08:00", "18:00"]', "08:00, 18:00", "08:00–18:00".
    The same values, so not a reason to send the report back for another round."""
    if not actual or any(isinstance(item, (dict, list)) for item in actual):
        return False
    wanted = [str(item).strip().casefold() for item in actual]
    with contextlib.suppress(json.JSONDecodeError):
        written = json.loads(text)
        if isinstance(written, list) and [str(item).strip().casefold() for item in written] == wanted:
            return True
    tokens = [token.casefold() for token in re.findall(r"[^\s,;\[\]\"'–—]+", text)]
    if tokens == wanted:
        return True
    pair = re.fullmatch(r"\s*(\S+?)\s*[-–—]\s*(\S+)\s*", text)
    return len(wanted) == 2 and pair is not None and [part.casefold() for part in pair.groups()] == wanted


def _as_number(text: str) -> float | None:
    try:
        return float(text.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def _lookup(record: dict[str, Any], path: str) -> tuple[bool, Any]:
    """A field by its dotted path, as the model names it. Through a list it takes
    the field of every item ("flags.code", "counted_intervals.start"). A first part
    the record does not have is dropped: the model often names where the record
    sits in the tool's answer ("totals.late_days" of the record that is the totals).
    Either way the value still has to be the one the tool returned."""
    parts = path.split(".")
    found, value = _walk(record, parts)
    if not found and len(parts) > 1 and parts[0] not in record:
        found, value = _walk(record, parts[1:])
    return found, value


def _walk(node: Any, parts: list[str]) -> tuple[bool, Any]:
    if not parts:
        return True, node
    if isinstance(node, list):
        values = [value for found, value in (_walk(item, parts) for item in node) if found]
        return (True, values) if values else (False, None)
    if not isinstance(node, dict) or parts[0] not in node:
        return False, None
    return _walk(node[parts[0]], parts[1:])


def _index(node: Any, records: dict[str, dict[str, Any]], seen: set[str]) -> None:
    if isinstance(node, list):
        for item in node:
            _index(item, records, seen)
        return
    if not isinstance(node, dict):
        return

    record_id = node.get("id")
    if isinstance(record_id, str) and record_id:
        records[record_id] = {**records.get(record_id, {}), **node}
    for key, value in node.items():
        if key.endswith("_id") and isinstance(value, str) and value:
            seen.add(value)
        elif key.endswith("_ids") and isinstance(value, list):
            seen.update(item for item in value if isinstance(item, str))
        _index(value, records, seen)


# Numbers in the summary ------------------------------------------------------------------


def _supported(token: str, numbers: frozenset[str]) -> bool:
    plain = re.sub(r"[ \u00a0\u202f]", "", token).replace(",", ".")
    if ":" in plain:
        return _clock(plain) in numbers
    parts = plain.split(".")
    if len(parts) == 2 and _decimal(plain) in numbers:
        return True
    # 28.10 or 28.10.2026: a date, each part a number the tools returned.
    return all(str(int(part)) in numbers for part in parts if part)


def _collect_numbers(node: Any, numbers: set[str]) -> None:
    if isinstance(node, dict):
        for value in node.values():
            _collect_numbers(value, numbers)
    elif isinstance(node, list):
        for item in node:
            _collect_numbers(item, numbers)
    elif isinstance(node, bool) or node is None:
        return
    elif isinstance(node, int):
        numbers.add(str(node))
        if abs(node) >= _MINUTES_AS_HOURS_FROM:  # minutes said as hours: 1824 → 30, 24, 30.4
            hours, rest = divmod(abs(node), 60)
            numbers.update({str(hours), str(rest), str(round(abs(node) / 60)), _decimal(f"{abs(node) / 60:.1f}")})
    elif isinstance(node, float):
        numbers.update({str(round(node)), _decimal(f"{node:.1f}"), _decimal(f"{node:.2f}")})
        if abs(node) >= 100:  # metres said as kilometres
            numbers.add(_decimal(f"{node / 1000:.1f}"))
    elif isinstance(node, str):
        for token in _NUMBER.findall(node):
            plain = re.sub(r"[ \u00a0\u202f]", "", token).replace(",", ".")
            if ":" in plain:
                numbers.add(_clock(plain))
            numbers.update(str(int(part)) for part in re.split(r"[:.]", plain) if part)
            if plain.count(".") == 1:
                numbers.add(_decimal(plain))


def _clock(text: str) -> str:
    hours, minutes = (text.split(":") + ["00"])[:2]
    return f"{int(hours)}:{minutes[:2]}"


def _decimal(text: str) -> str:
    value = float(text)
    return str(int(value)) if value == int(value) else f"{value:g}"
