"""Ask the agent team a question from the terminal and get recommendation cards.

    python -m nazar_agent "Who did not show up on the Chilonzor site yesterday?"
    python -m nazar_agent --analyst timesheet "Anything to check before paying for last week?"
    python -m nazar_agent --trace "..."       # also print tool calls and the fact checker's rounds
    python -m nazar_agent --no-review "..."   # analyst only: no skeptic, no actions
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path

from pydantic import BaseModel

from nazar_agent.config import Settings, get_settings
from nazar_agent.findings import Analysis, analyze
from nazar_agent.llm.pricing import cost_usd
from nazar_agent.nazar.connect import connect
from nazar_agent.recommendations import Card, Recommendations, Team, Usage, ValidatedAction, recommend
from nazar_agent.roles import attendance_analyst, timesheet_analyst
from nazar_agent.tools import PhotoInspector, analyst_tools

ANALYSTS = {"attendance": attendance_analyst, "timesheet": timesheet_analyst}


@dataclass(frozen=True, slots=True)
class Outcome:
    analysis: Analysis
    recommendations: Recommendations | None
    photos: Usage | None = None  # what the photo inspector spent, if it was asked


async def ask(question: str, analyst: str, *, review: bool, settings: Settings | None = None) -> Outcome:
    settings = settings or get_settings()
    nazar = connect(settings).client()
    analyst_llm = settings.analyst_llm()
    skeptic_llm = settings.skeptic_llm()
    # The photo inspector runs on the open-weights model, like the skeptic.
    photos = PhotoInspector(skeptic_llm, settings.skeptic_model)
    today = settings.now().date()
    try:
        tools = analyst_tools(analyst, nazar, photos)
        role = ANALYSTS[analyst](model=settings.analyst_model, tools=tools, today=today)
        analysis = await analyze(role, analyst_llm, question)
        photo_usage = (
            Usage("photos", photos.model, photos.input_tokens, photos.output_tokens) if photos.input_tokens else None
        )
        if not review:
            return Outcome(analysis, None, photo_usage)
        team = Team(
            skeptic_llm=skeptic_llm,
            skeptic_model=settings.skeptic_model,
            writer_llm=analyst_llm,
            writer_model=settings.analyst_model,
        )
        return Outcome(analysis, await recommend(analysis, team, nazar, today), photo_usage)
    finally:
        await nazar.aclose()
        await analyst_llm.aclose()
        await skeptic_llm.aclose()


def render(outcome: Outcome) -> str:
    report = outcome.analysis.report
    lines = [report.summary, ""]
    if outcome.recommendations is None:
        for number, finding in enumerate(report.findings, start=1):
            lines.append(f"{number}. {finding.title} — {finding.subject_name} [{finding.confidence}]")
            lines.extend(f"   ✓ {fact.text}" for fact in finding.facts)
            lines.append("")
    else:
        for number, card in enumerate(outcome.recommendations.cards, start=1):
            lines.extend(_card_lines(number, card))
        lines.extend(f"! {note}" for note in outcome.recommendations.notes)
    lines.extend(f"! {gap}" for gap in report.data_gaps)
    for item in outcome.analysis.rejected:
        target = item.fact or f"finding «{item.finding}»"
        lines.append(f"✗ removed by the fact checker — {target}: {'; '.join(item.reasons)}")
    return "\n".join(lines).rstrip()


def _card_lines(number: int, card: Card) -> list[str]:
    finding = card.finding
    skeptic = card.skeptic.verdict if card.skeptic else "no opinion"
    lines = [
        f"{number}. {finding.title} — {finding.subject_name}",
        f"   confidence: {card.confidence} (analyst: {card.analyst_confidence}, skeptic: {skeptic})",
    ]
    for fact in finding.facts:
        refs = ", ".join(f"{ref.kind}:{ref.id[:8]}" for ref in fact.evidence)
        lines.append(f"   ✓ {fact.text} ({refs})")
    lines.append(f"   ? {finding.hypothesis}")
    lines.extend(f"   ↔ {alternative}" for alternative in card.alternatives)
    lines.extend(f"   → {step}" for step in card.check_steps)
    if card.actions_why:
        lines.append(f"   ≡ {card.actions_why}")
    lines.extend(f"   [{action.level}] {_action_text(action)}" for action in card.actions)
    lines.extend(f"   ✗ action {action.name}: {'; '.join(action.problems)}" for action in card.rejected_actions)
    lines.append("")
    return lines


def _action_text(action: ValidatedAction) -> str:
    params = action.params.model_dump(mode="json") if action.params else {}
    text = f"{action.name} {json.dumps(params, ensure_ascii=False)}"
    if action.preview is not None:
        text += f"\n      before: {action.preview.before}\n      after:  {action.preview.after}"
    return text


def usage_of(outcome: Outcome, analyst_model: str) -> list[Usage]:
    analysis = outcome.analysis
    usage = [Usage("analyst", analyst_model, analysis.input_tokens, analysis.output_tokens)]
    if outcome.photos is not None:
        usage.append(outcome.photos)
    if outcome.recommendations is not None:
        usage.extend(outcome.recommendations.usage)
    return usage


def total_cost(outcome: Outcome, analyst_model: str) -> float:
    costs = (cost_usd(item.model, item.input_tokens, item.output_tokens) for item in usage_of(outcome, analyst_model))
    return sum(cost for cost in costs if cost is not None)


def render_stats(outcome: Outcome, analyst_model: str) -> str:
    analysis = outcome.analysis
    usage = usage_of(outcome, analyst_model)
    costs = [cost_usd(item.model, item.input_tokens, item.output_tokens) for item in usage]
    parts = [
        f"{item.role} {item.model}: {item.input_tokens}/{item.output_tokens} tokens"
        + (f" ~${cost:.4f}" if cost is not None else "")
        for item, cost in zip(usage, costs, strict=True)
    ]
    total = sum(cost for cost in costs if cost is not None)
    return (
        f"{analysis.run.model_turns} investigation turns, {len(analysis.run.steps)} tool calls, "
        f"{analysis.corrections} fact-checker rounds. " + "; ".join(parts) + f". Total ~${total:.4f}"
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


def json_default(value: object) -> object:
    if isinstance(value, BaseModel):
        return value.model_dump(mode="json")
    return str(value)


def main() -> int:
    parser = argparse.ArgumentParser(prog="nazar-agent", description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--analyst", choices=sorted(ANALYSTS), default="attendance")
    parser.add_argument("--trace", action="store_true", help="print tool calls and the fact checker's rounds")
    parser.add_argument("--no-review", action="store_true", help="skip the skeptic and the actions")
    parser.add_argument("--dump", type=Path, help="also save the whole outcome as JSON to this file")
    args = parser.parse_args()

    outcome = asyncio.run(ask(args.question, args.analyst, review=not args.no_review))
    if args.dump:
        args.dump.write_text(json.dumps(asdict(outcome), ensure_ascii=False, indent=1, default=json_default), encoding="utf-8")
    if args.trace:
        _print_trace(outcome.analysis)
    print(render(outcome))
    print()
    print(render_stats(outcome, get_settings().analyst_model), file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
