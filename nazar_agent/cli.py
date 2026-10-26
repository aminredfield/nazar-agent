"""Ask an analyst a question from the terminal and get a verified report.

    python -m nazar_agent "Who did not show up on the Chilonzor site yesterday?"
    python -m nazar_agent --analyst timesheet "Anything to check before paying for last week?"
    python -m nazar_agent --trace "..."    # also print every tool call
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime

from nazar_agent.config import get_settings
from nazar_agent.findings import Analysis, analyze
from nazar_agent.llm import MetaResponsesClient
from nazar_agent.llm.pricing import cost_usd
from nazar_agent.nazar import NazarClient
from nazar_agent.roles import attendance_analyst, timesheet_analyst
from nazar_agent.tools import attendance_tools, timesheet_tools
from nazar_agent.tools.common import COMPANY_TZ

ANALYSTS = {
    "attendance": (attendance_analyst, attendance_tools),
    "timesheet": (timesheet_analyst, timesheet_tools),
}


async def ask(question: str, analyst: str) -> Analysis:
    settings = get_settings()
    nazar = NazarClient(
        base_url=settings.nazar_base_url,
        email=settings.nazar_email,
        password=settings.nazar_password.get_secret_value(),
        timeout_seconds=settings.nazar_timeout_seconds,
    )
    llm = MetaResponsesClient(
        api_key=settings.model_api_key.get_secret_value(),
        base_url=settings.meta_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    make_role, make_tools = ANALYSTS[analyst]
    try:
        role = make_role(
            model=settings.analyst_model,
            tools=make_tools(nazar),
            today=datetime.now(COMPANY_TZ).date(),
        )
        return await analyze(role, llm, question)
    finally:
        await nazar.aclose()
        await llm.aclose()


def render(analysis: Analysis) -> str:
    report = analysis.report
    lines = [report.summary, ""]
    for number, finding in enumerate(report.findings, start=1):
        lines.append(f"{number}. {finding.title} — {finding.subject_name} [confidence: {finding.confidence}]")
        for fact in finding.facts:
            refs = ", ".join(f"{ref.kind}:{ref.id[:8]}" for ref in fact.evidence)
            lines.append(f"   ✓ {fact.text} ({refs})")
        lines.append(f"   ? {finding.hypothesis}")
        for alternative in finding.alternatives:
            lines.append(f"   ↔ {alternative}")
        lines.append("")
    for gap in report.data_gaps:
        lines.append(f"! {gap}")
    if analysis.rejected:
        lines.append("Removed by the fact checker:")
        for item in analysis.rejected:
            target = item.fact or f"finding «{item.finding}»"
            lines.append(f"   ✗ {target}: {'; '.join(item.reasons)}")
    return "\n".join(lines).rstrip()


def render_stats(analysis: Analysis, model: str) -> str:
    cost = cost_usd(model, analysis.input_tokens, analysis.output_tokens)
    cost_text = f", ~${cost:.4f}" if cost is not None else ""
    return (
        f"{analysis.run.model_turns} investigation turns, {len(analysis.run.steps)} tool calls, "
        f"{analysis.corrections} corrections by the fact checker "
        f"({'clean' if analysis.clean_first_time else 'not clean'} first time), "
        f"{analysis.input_tokens} input / {analysis.output_tokens} output tokens{cost_text}"
    )


def _print_trace(analysis: Analysis) -> None:
    for step in analysis.run.steps:
        status = f"error: {step.error}" if step.error else "ok"
        arguments = json.dumps(step.arguments, ensure_ascii=False, default=str)
        print(f"  → {step.name}({arguments}) [{status}, {step.duration_ms} ms]")
    for number, issues in enumerate(analysis.caught, start=1):
        print(f"  fact checker, round {number}:")
        for issue in issues:
            print(f"    ✗ {issue}")


def main() -> int:
    parser = argparse.ArgumentParser(prog="nazar-agent", description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--analyst", choices=sorted(ANALYSTS), default="attendance")
    parser.add_argument("--trace", action="store_true", help="print every tool call")
    args = parser.parse_args()

    analysis = asyncio.run(ask(args.question, args.analyst))
    if args.trace:
        _print_trace(analysis)
    print(render(analysis))
    print()
    print(render_stats(analysis, get_settings().analyst_model), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
