"""The skeptic: an independent second opinion before anything reaches the owner.

It runs on a different model than the analysts and sees only the verified
facts and the NAZAR records behind them — not the analyst's hypothesis, title
or confidence — so it cannot simply agree with a conclusion. Its job is to
protect people from being wrongly suspected: find the innocent explanation
that fits the facts best and say whether the concern really holds.

The final confidence is set by code from both opinions (``merge_confidence``),
never by either model alone.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nazar_agent.engine import tool_schema
from nazar_agent.findings import AnalystReport, Evidence
from nazar_agent.findings.models import Confidence
from nazar_agent.llm import LLMClient

Verdict = Literal["concern_supported", "inconclusive", "innocent_likely"]
_RANK: dict[Confidence, int] = {"low": 0, "medium": 1, "high": 2}

INSTRUCTIONS = """\
You are the skeptic in a team of agents that reviews time-and-attendance data
for business owners in Uzbekistan: construction sites, shops, cafés, offices.
Employees check in from their phone with face recognition and the site's
geofence; GPS is often weak indoors and phones are sometimes shared.

Other agents collected the facts below. You do not see what they concluded.
Your job is to protect people from being wrongly suspected. For each case:
- find the innocent explanation that fits ALL the facts best;
- decide whether the facts really support a concern:
  concern_supported — innocent explanations fit the facts poorly;
  inconclusive — a concern and an innocent explanation fit about equally;
  innocent_likely — an innocent explanation fits better;
- say what the owner could check to settle it (ask someone, look at a photo,
  compare with another day).

Use only the facts and records given; do not invent data. Write in the
language the facts are written in, for a business owner, not a programmer:
- each innocent explanation and each check is one short sentence in plain
  words, at most three of each; the detail goes into reasoning;
- never write field names, codes or values like open_shift=true.
"""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SkepticAssessment(_Strict):
    case: int = Field(description="The case number as given.")
    verdict: Verdict
    reasoning: str = Field(description="Two or three sentences.")
    innocent_explanations: list[str]
    what_would_settle_it: list[str]


class SkepticReport(_Strict):
    assessments: list[SkepticAssessment]


SKEPTIC_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "SkepticReport",
    "schema": tool_schema(SkepticReport),
    "strict": True,
}


@dataclass(frozen=True, slots=True)
class SkepticView:
    verdict: Verdict
    reasoning: str
    innocent_explanations: tuple[str, ...]
    what_would_settle_it: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class SkepticResult:
    views: dict[int, SkepticView]  # by finding index
    error: str | None  # why the skeptic gave no opinion, if it did not
    input_tokens: int
    output_tokens: int


async def run_skeptic(llm: LLMClient, model: str, report: AnalystReport, evidence: Evidence) -> SkepticResult:
    if not report.findings:
        return SkepticResult(views={}, error=None, input_tokens=0, output_tokens=0)

    turn = await llm.respond(
        model=model,
        instructions=INSTRUCTIONS,
        input=cases_text(report, evidence),
        tools=[],
        text_format=SKEPTIC_FORMAT,
    )
    try:
        parsed = SkepticReport.model_validate_json(turn.text)
    except ValidationError as exc:
        return SkepticResult({}, f"the skeptic's answer did not match the schema: {exc.error_count()} errors", turn.input_tokens, turn.output_tokens)

    views = {
        assessment.case - 1: SkepticView(
            verdict=assessment.verdict,
            reasoning=assessment.reasoning,
            innocent_explanations=tuple(assessment.innocent_explanations),
            what_would_settle_it=tuple(assessment.what_would_settle_it),
        )
        for assessment in parsed.assessments
        if 1 <= assessment.case <= len(report.findings)
    }
    missing = len(report.findings) - len(views)
    error = f"no opinion on {missing} case(s)" if missing else None
    return SkepticResult(views, error, turn.input_tokens, turn.output_tokens)


def cases_text(report: AnalystReport, evidence: Evidence) -> str:
    """The skeptic's whole input: facts and their records, nothing concluded."""
    cases = []
    for number, finding in enumerate(report.findings, start=1):
        cases.append({
            "case": number,
            "about": finding.subject_name,
            "facts": [
                {
                    "text": fact.text,
                    "records": [evidence.records[ref.id] for ref in fact.evidence if ref.id in evidence.records],
                }
                for fact in finding.facts
            ],
        })
    return json.dumps({"cases": cases}, ensure_ascii=False, default=str)


def merge_confidence(analyst: Confidence, skeptic: SkepticView | None) -> Confidence:
    """Both opinions, by fixed rules. Without a skeptic, never above medium."""
    if skeptic is None or skeptic.verdict == "inconclusive":
        return _lower(analyst, "medium")
    if skeptic.verdict == "innocent_likely":
        return "low"
    return analyst


def _lower(first: Confidence, second: Confidence) -> Confidence:
    return first if _RANK[first] <= _RANK[second] else second
