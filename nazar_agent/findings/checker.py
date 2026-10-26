"""The fact checker: plain code that verifies a report against the tool outputs.

It cannot be talked into anything. It knows only what the tools returned in
this run (the trace) and checks that

- every fact cites at least one record, and every cited id was actually seen;
- every claim names a record that was returned, a field that record has, and
  the value the tool gave for it;
- every finding is about a subject that exists and offers at least one
  innocent explanation.

Records are dicts with an ``id`` in any tool output; the same id seen in a
list and in a detail view is merged. Any other ``*_id`` value counts as a
reference that exists, but a claim needs the record itself.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from nazar_agent.engine import ToolStep
from nazar_agent.findings.models import AnalystReport, Claim, Finding

IssueCode = Literal[
    "no_evidence",
    "unknown_ref",
    "unknown_record",
    "missing_field",
    "value_mismatch",
    "unknown_subject",
    "no_alternatives",
    "no_facts",
]

# Counts (minutes, days) are integers and must match exactly. Measurements
# (distances, scores) are floats and agree within 1%: a model may write 6600
# for 6600.0 or round 6587.3 to 6590, but 6.6 for 6600.0 is a different number.
_RELATIVE_TOLERANCE = 0.01
_ABSOLUTE_TOLERANCE = 0.5
# A claimed text may be part of the tool's text ("10:14" of a timestamp, a
# first name of a full name), but not a one- or two-letter fragment.
_MIN_PARTIAL_TEXT = 4


@dataclass(frozen=True, slots=True)
class Issue:
    finding: int  # 0-based index in the report
    fact: int | None  # None when the issue is about the finding itself
    code: IssueCode
    detail: str

    def describe(self) -> str:
        where = f"finding {self.finding + 1}"
        if self.fact is not None:
            where += f", fact {self.fact + 1}"
        return f"{where}: {self.detail}"


@dataclass(frozen=True, slots=True)
class Evidence:
    """Everything the tools returned in one run, indexed for lookups."""

    records: dict[str, dict[str, Any]]
    seen_ids: frozenset[str]

    @classmethod
    def from_steps(cls, steps: tuple[ToolStep, ...]) -> Evidence:
        records: dict[str, dict[str, Any]] = {}
        seen: set[str] = set()
        for step in steps:
            if step.error is None:
                _index(step.output, records, seen)
        return cls(records=records, seen_ids=frozenset(seen | set(records)))


def check_report(report: AnalystReport, evidence: Evidence) -> list[Issue]:
    issues: list[Issue] = []
    for index, finding in enumerate(report.findings):
        issues.extend(_check_finding(index, finding, evidence))
    return issues


def _check_finding(index: int, finding: Finding, evidence: Evidence) -> list[Issue]:
    issues: list[Issue] = []
    if finding.subject_kind != "company" and finding.subject_id not in evidence.seen_ids:
        issues.append(Issue(index, None, "unknown_subject", f"subject id {finding.subject_id!r} was not returned by any tool"))
    if not any(text.strip() for text in finding.alternatives):
        issues.append(Issue(index, None, "no_alternatives", "give at least one innocent explanation"))
    if not finding.facts:
        issues.append(Issue(index, None, "no_facts", "a finding needs at least one fact"))

    for fact_index, fact in enumerate(finding.facts):
        if not fact.evidence:
            issues.append(Issue(index, fact_index, "no_evidence", "the fact cites no record"))
        for ref in fact.evidence:
            if ref.id not in evidence.seen_ids:
                issues.append(Issue(index, fact_index, "unknown_ref", f"{ref.kind}:{ref.id} was not returned by any tool"))
        for claim in fact.claims:
            problem = _check_claim(claim, evidence)
            if problem is not None:
                issues.append(Issue(index, fact_index, problem[0], problem[1]))
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
        return any(values_match(text, item) for item in actual) or text == str(actual)
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


def _as_number(text: str) -> float | None:
    try:
        return float(text.replace(",", ".").replace(" ", ""))
    except ValueError:
        return None


def _lookup(record: dict[str, Any], path: str) -> tuple[bool, Any]:
    current: Any = record
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return False, None
        current = current[part]
    return True, current


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
