"""From a question to a verified report.

1. The analyst role investigates with its tools (``run_role``).
2. Continuing the same conversation, it writes a report with structured
   output — the answer is generated to the ``AnalystReport`` schema.
3. The fact checker verifies the report against the tool outputs. If it
   finds problems, they go back to the model as a list, up to
   ``max_corrections`` times. Tools are not offered again: a fix may only use
   data already seen.
4. Whatever still fails is removed from the report and kept, with the
   reasons, in ``rejected`` — nothing unverified reaches the owner, and the
   trace shows what was cut.
5. A report with no findings after an investigation is asked for once more:
   in evals one came back as a summary "Placeholder" right after the photo
   inspector had described four photos of a screen.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError

from nazar_agent.engine import Role, RoleRun, run_role, tool_schema
from nazar_agent.findings.checker import Evidence, Issue, check_report
from nazar_agent.findings.models import AnalystReport, Finding
from nazar_agent.llm import LLMClient
from nazar_agent.progress import emit
from nazar_agent.usage import count

DEFAULT_MAX_CORRECTIONS = 2
log = logging.getLogger(__name__)

REPORT_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "AnalystReport",
    "schema": tool_schema(AnalystReport),
    "strict": True,
}

REPORT_REQUEST = """\
Now write your report for the owner, as JSON matching the schema.

- Use only what the tools returned in this conversation.
- Each fact cites the records it comes from in evidence, with ids exactly as
  returned and the right kind: event, employee, object, timesheet_day (ids
  like "<employee_id>:<date>"), setup_finding.
- Put every number, time, status or name a fact relies on into claims: the
  record id, the field name exactly as in the tool output (dots for nested
  fields, e.g. previous_event.at) and the value exactly as returned (6600.0,
  not 6.6 km). The text itself may say it in human form.
- One finding per employee, site or company-wide pattern worth checking. If
  nothing is worth checking, return no findings and say so in the summary.
  Missing or not yet final data (an open period, a truncated result) is a
  data gap, not a finding.
- The title says what is worth checking; the subject's name is shown next to
  it, so do not repeat the name in the title.
- The hypothesis is a hypothesis. Alternatives hold at least one innocent
  explanation. Confidence is low, medium or high.
- Write the summary, titles, facts, hypothesis, alternatives and data gaps
  in the language of the owner's question, with no internal codes or field
  names.
"""


EMPTY_REPORT_CHECK = """\
Your report has no findings. Look again at what the tools returned in this
conversation. If nothing there is worth checking, return the empty report
again with a summary that says so in plain words for the owner; otherwise
return the full report with the findings.
"""


class ReportError(Exception):
    """The model did not produce a report that matches the schema."""


@dataclass(frozen=True, slots=True)
class Rejected:
    finding: str
    fact: str | None  # None when the whole finding was rejected
    reasons: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Analysis:
    run: RoleRun
    report: AnalystReport  # verified content only
    rejected: tuple[Rejected, ...]
    corrections: int  # times the checker sent the report back
    clean_first_time: bool
    # What the checker sent back each time — the visible trace of its work.
    caught: tuple[tuple[str, ...], ...]
    input_tokens: int
    output_tokens: int


async def analyze(
    role: Role,
    llm: LLMClient,
    question: str,
    *,
    max_corrections: int = DEFAULT_MAX_CORRECTIONS,
) -> Analysis:
    run = await run_role(role, llm, question)
    evidence = Evidence.from_steps(run.steps)

    conversation = [*run.conversation, {"role": "user", "content": REPORT_REQUEST}]
    input_tokens, output_tokens = run.input_tokens, run.output_tokens
    report: AnalystReport | None = None
    issues: list[Issue] = []
    clean_first_time = False
    caught: list[tuple[str, ...]] = []

    for attempt in range(max_corrections + 1):
        log.info("%s: report, attempt %d", role.name, attempt + 1)
        emit("report", agent=role.name, attempt=attempt + 1)
        turn = await llm.respond(
            model=role.model,
            instructions=role.instructions,
            input=list(conversation),
            tools=[],
            text_format=REPORT_FORMAT,
        )
        count(role.name, role.model, turn.input_tokens, turn.output_tokens)
        input_tokens += turn.input_tokens
        output_tokens += turn.output_tokens
        conversation.append({"role": "assistant", "content": turn.text})

        try:
            report = AnalystReport.model_validate_json(turn.text)
        except ValidationError as exc:
            report = None
            conversation.append({
                "role": "user",
                "content": f"The report does not match the schema: {exc.errors()[:5]}. Return the full report again.",
            })
            caught.append(("the report did not match the schema",))
            continue

        issues = check_report(report, evidence)
        emit(
            "checker",
            round=attempt + 1,
            findings=len(report.findings),
            facts=sum(len(finding.facts) for finding in report.findings),
            issues=[issue.describe() for issue in issues][:5],
        )
        if attempt == 0:
            clean_first_time = not issues
        if not issues:
            break
        if attempt < max_corrections:
            caught.append(tuple(issue.describe() for issue in issues))
        conversation.append({"role": "user", "content": correction_request(issues)})

    if report is None:
        raise ReportError("No valid report after corrections.")

    if not report.findings and not issues and run.steps:
        emit("checker", round=0, findings=0, facts=0, issues=["в отчёте нет находок после расследования — спрашиваю ещё раз"])
        conversation.append({"role": "user", "content": EMPTY_REPORT_CHECK})
        turn = await llm.respond(
            model=role.model,
            instructions=role.instructions,
            input=list(conversation),
            tools=[],
            text_format=REPORT_FORMAT,
        )
        count(role.name, role.model, turn.input_tokens, turn.output_tokens)
        input_tokens += turn.input_tokens
        output_tokens += turn.output_tokens
        caught.append(("the report had no findings after an investigation; asked once more",))
        try:
            again = AnalystReport.model_validate_json(turn.text)
        except ValidationError:
            again = None  # keep the empty report rather than nothing
        if again is not None:
            # No further correction rounds: whatever fails now is dropped below.
            report, issues = again, check_report(again, evidence)

    verified, rejected = drop_failing(report, issues)
    return Analysis(
        run=run,
        report=verified,
        rejected=rejected,
        corrections=attempt,
        clean_first_time=clean_first_time,
        caught=tuple(caught),
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )


def correction_request(issues: list[Issue]) -> str:
    lines = "\n".join(f"- {issue.describe()}" for issue in issues)
    return (
        "The fact checker compared your report with the tool outputs and found problems:\n"
        f"{lines}\n"
        "Fix each one using only data the tools returned, or remove the fact or the "
        "finding if the data does not support it. Return the full report again."
    )


def drop_failing(report: AnalystReport, issues: list[Issue]) -> tuple[AnalystReport, tuple[Rejected, ...]]:
    """Remove what failed: a fact with an issue, a finding with its own issue or no facts left."""
    finding_issues: dict[int, list[str]] = defaultdict(list)
    fact_issues: dict[tuple[int, int], list[str]] = defaultdict(list)
    for issue in issues:
        if issue.fact is None:
            finding_issues[issue.finding].append(issue.detail)
        else:
            fact_issues[(issue.finding, issue.fact)].append(issue.detail)

    kept: list[Finding] = []
    rejected: list[Rejected] = []
    for index, finding in enumerate(report.findings):
        if index in finding_issues:
            rejected.append(Rejected(finding.title, None, tuple(finding_issues[index])))
            continue
        facts = []
        for fact_index, fact in enumerate(finding.facts):
            reasons = fact_issues.get((index, fact_index))
            if reasons:
                rejected.append(Rejected(finding.title, fact.text, tuple(reasons)))
            else:
                facts.append(fact)
        if not facts:
            rejected.append(Rejected(finding.title, None, ("no verified facts left",)))
            continue
        kept.append(finding.model_copy(update={"facts": facts}))

    return report.model_copy(update={"findings": kept}), tuple(rejected)
