"""What an analyst hands over: findings, each a set of facts plus a hypothesis.

A fact is only what a tool returned, and it says where it came from. Every
value a fact relies on (a distance, a time, a count) is also stated as a
``Claim`` — record id, field, value — so code can look it up in the tool
outputs and confirm it, instead of trusting the sentence.

No field has a default: the report is produced with structured output in
strict mode, which needs every property listed as required.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceKind = Literal["event", "employee", "object", "device", "timesheet_day", "setup_finding"]
SubjectKind = Literal["employee", "object", "company"]
Confidence = Literal["low", "medium", "high"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvidenceRef(_Strict):
    kind: EvidenceKind
    id: str = Field(description="The record id exactly as a tool returned it.")


class Claim(_Strict):
    ref: EvidenceRef
    field: str = Field(description="Field of that record as the tool named it; dots for nesting, e.g. previous_event.at.")
    value: str = Field(description="The value exactly as the tool returned it, as text.")


class Fact(_Strict):
    text: str = Field(description="One observation in plain words for a business owner.")
    evidence: list[EvidenceRef] = Field(description="Records this fact comes from; at least one.")
    claims: list[Claim] = Field(description="Every number, time or status the text relies on.")


class Finding(_Strict):
    title: str = Field(description="Short, neutral: what is worth checking.")
    subject_kind: SubjectKind
    subject_id: str = Field(description="Id of the employee or object; empty for the whole company.")
    subject_name: str
    facts: list[Fact]
    hypothesis: str = Field(description="One possible explanation, stated as a hypothesis.")
    alternatives: list[str] = Field(description="Innocent explanations that fit the same facts; at least one.")
    confidence: Confidence


class AnalystReport(_Strict):
    summary: str = Field(description="Two or three sentences for the owner.")
    findings: list[Finding]
    data_gaps: list[str] = Field(description="What could not be seen: truncated results, failed tools.")
