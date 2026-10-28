"""Ask the whole question bank on the demo company, stopped at BANK_NOW, and check every answer.

    python -m nazar_agent.evals.bank                  # all questions
    python -m nazar_agent.evals.bank --intent I J     # only off-topic and forbidden
    python -m nazar_agent.evals.bank --id A01 C01

Every question goes through the whole team as in the window — dispatcher,
analyst, fact checker, skeptic, writer — and nothing proposed is applied.
Each answer and its checks are saved, with a table by kind of question.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from nazar_agent.actions import ProposalStore
from nazar_agent.assistant import Assistant
from nazar_agent.cli import json_default
from nazar_agent.config import Settings
from nazar_agent.evals.checks import Run
from nazar_agent.evals.questions import BANK, BANK_NOW, INTENTS, Question
from nazar_agent.evals.truth import Truth
from nazar_agent.nazar.connect import NazarLink, connect

DEFAULT_OUT = Path("evals-results")


def bank_settings(folder: Path) -> Settings:
    return Settings(demo_mode=True, demo_now=BANK_NOW, telegram_bot_token=None, agent_db_path=str(folder / "agent.sqlite3"))


async def ask_one(question: Question, settings: Settings, link: NazarLink, store: ProposalStore, truth: Truth) -> dict[str, Any]:
    started = time.monotonic()
    nazar = link.client()
    assistant = Assistant(settings, store, service=nazar)
    run, view, cost = Run(), None, None
    try:
        answer = await assistant.ask(question.text)
        view = answer.view
        cost = view["stats"]["cost_usd"]
        outcome = json.loads(json.dumps(
            {"analysis": asdict(answer.analysis), "recommendations": asdict(answer.recommendations)},
            ensure_ascii=False, default=json_default,
        ))
        run = Run(
            summary=view["summary"],
            text=owner_text(view),
            tools=[(step.name, step.arguments if isinstance(step.arguments, dict) else {}) for step in answer.analysis.run.steps],
            cards=view["cards"],
            outcome=outcome,
        )
    except Exception as exc:  # a failed answer is a result too
        run = Run(error=f"{type(exc).__name__}: {exc}")
    finally:
        await nazar.aclose()
    failed = {check.label: why for check in question.checks if (why := check(run, truth)) is not None}
    return {
        "id": question.id, "intent": question.intent, "lang": question.lang, "question": question.text,
        "passed": not failed, "failed": failed, "checks": len(question.checks),
        "summary": run.summary, "tools": [name for name, _ in run.tools], "cards": len(run.cards),
        "actions": [action["name"] for action in run.actions], "error": run.error,
        "cost_usd": cost, "seconds": round(time.monotonic() - started), "view": view,
    }


def owner_text(view: dict[str, Any]) -> str:
    """Everything the owner reads in the answer."""
    parts = [view["summary"], *view.get("data_gaps", []), *view.get("notes", [])]
    for card in view["cards"]:
        parts += [card["title"], card.get("hypothesis") or "", *card.get("alternatives", []), *card.get("check_steps", [])]
        parts += [fact["text"] for fact in card["facts"]]
    return "\n".join(part for part in parts if part)


def table(results: list[dict[str, Any]]) -> str:
    by_intent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_intent[result["intent"]].append(result)
    lines = ["| | Kind of question | Passed | Failed checks |", "|---|---|---|---|"]
    for intent, items in sorted(by_intent.items()):
        failures: dict[str, int] = defaultdict(int)
        for item in items:
            for label in item["failed"]:
                failures[label] += 1
        failed = ", ".join(f"{label} ×{times}" for label, times in sorted(failures.items(), key=lambda kv: -kv[1])) or "—"
        lines.append(f"| {intent} | {INTENTS[intent]} | {sum(item['passed'] for item in items)}/{len(items)} | {failed} |")
    passed = sum(result["passed"] for result in results)
    costs = [result["cost_usd"] for result in results if result["cost_usd"] is not None]
    lines.append(f"| | **All** | **{passed}/{len(results)}** | avg ${sum(costs) / max(len(costs), 1):.3f} per question |")
    return "\n".join(lines)


def line(result: dict[str, Any]) -> str:
    mark = "PASS" if result["passed"] else "FAIL"
    failed = "; ".join(f"{label}: {why}" for label, why in result["failed"].items())
    return f"{result['id']} {mark} {result['question'][:60]}" + (f" — {failed}" if failed else "")


async def main_async(questions: list[Question], parallel: int, folder: Path) -> list[dict[str, Any]]:
    settings = bank_settings(folder)
    link = connect(settings)
    assert link.demo is not None
    truth = Truth(link.demo.world)
    store = ProposalStore(settings.agent_db_path)
    limit = asyncio.Semaphore(parallel)

    async def guarded(question: Question) -> dict[str, Any]:
        async with limit:
            result = await ask_one(question, settings, link, store, truth)
        (folder / f"{question.id}.json").write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
        print(line(result), flush=True)
        return result

    return list(await asyncio.gather(*(guarded(question) for question in questions)))


def main() -> int:
    parser = argparse.ArgumentParser(prog="nazar_agent.evals.bank", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--intent", nargs="+", choices=sorted(INTENTS), help="only these kinds of question")
    parser.add_argument("--id", nargs="+", help="only these questions")
    parser.add_argument("--parallel", type=int, default=4)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    questions = [
        question for question in BANK
        if (not args.intent or question.intent in args.intent) and (not args.id or question.id in args.id)
    ]
    folder = args.out / f"bank-{datetime.now().strftime('%Y-%m-%d-%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    print(f"{len(questions)} questions on the demo company at {BANK_NOW.isoformat()}", file=sys.stderr)
    results = sorted(asyncio.run(main_async(questions, args.parallel, folder)), key=lambda result: result["id"])
    (folder / "results.json").write_text(
        json.dumps([{key: value for key, value in result.items() if key != "view"} for result in results], ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    summary = table(results)
    (folder / "summary.md").write_text(summary + "\n", encoding="utf-8")
    print()
    print(summary)
    print(f"\nSaved to {folder}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
