"""Run the planted cases against a NAZAR with the construction demo company and score them.

    python -m nazar_agent.evals --runs 5                 # every case five times
    python -m nazar_agent.evals --case dead_souls --runs 10

The agent signs in with NAZAR_EMAIL / NAZAR_PASSWORD, which must be the
construction demo's owner. Every run's whole outcome is saved next to the
scores, so a surprising number can be checked against what the agents saw.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any

from nazar_agent.cli import ask, json_default, total_cost
from nazar_agent.config import get_settings
from nazar_agent.evals.cases import (
    CASES,
    LIST_DUPLICATE_OF_EMPLOYEE,
    LIST_SURNAME_ONLY,
    LIST_TWICE,
    Case,
)
from nazar_agent.evals.score import score_analysis, score_import
from nazar_agent.evals.summary import summarize
from nazar_agent.nazar import NazarClient
from nazar_agent.onboarding import Site, read_list, review
from nazar_agent.tools.common import call_nazar

DEFAULT_OUT = Path("evals-results")


async def run_analysis(case: Case, out: Path) -> dict[str, Any]:
    started = time.monotonic()
    outcome = await ask(case.question, case.analyst, review=True)
    data = json.loads(json.dumps(asdict(outcome), ensure_ascii=False, default=json_default))
    out.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    result = score_analysis(data, case.name)
    result["cost_usd"] = round(total_cost(outcome, get_settings().analyst_model), 4)
    result["seconds"] = round(time.monotonic() - started)
    return result


async def run_import(case: Case, out: Path) -> dict[str, Any]:
    settings = get_settings()
    started = time.monotonic()
    llm = settings.analyst_llm()
    nazar = NazarClient(
        base_url=settings.nazar_base_url,
        email=settings.nazar_email,
        password=settings.nazar_password.get_secret_value(),
        timeout_seconds=settings.nazar_timeout_seconds,
    )
    try:
        people = await read_list(llm, settings.analyst_model, text=case.question)
        objects = await call_nazar(nazar.get("/objects", {"include_inactive": False}))
        employees = await call_nazar(nazar.get("/employees"))
    finally:
        await nazar.aclose()
        await llm.aclose()
    sites = [Site(item["id"], item["name"], item.get("address") or "") for item in objects]
    rows = [asdict(row) for row in review(people, sites, employees)]
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    expected = {LIST_DUPLICATE_OF_EMPLOYEE: "duplicate", LIST_TWICE: "twice", LIST_SURNAME_ONLY: "surname"}
    result = score_import(rows, expected)
    result["seconds"] = round(time.monotonic() - started)
    return result


async def run_one(case: Case, number: int, folder: Path, limit: asyncio.Semaphore) -> dict[str, Any]:
    out = folder / f"{case.name}-{number:02d}.json"
    async with limit:
        try:
            if case.kind == "import":
                result = await run_import(case, out)
            else:
                result = await run_analysis(case, out)
        except Exception as exc:  # a failed run is a result too: it counts against the case
            result = {"case": case.name, "passed": False, "error": f"{type(exc).__name__}: {exc}"}
    result["run"] = number
    print(_line(result), flush=True)
    return result


def _line(result: dict[str, Any]) -> str:
    if "error" in result:
        return f"{result['case']} #{result['run']}: ERROR {result['error']}"
    mark = "PASS" if result["passed"] else "FAIL"
    details = {key: result[key] for key in ("found", "missed", "falsely_accused", "schedule_found", "schedule_fix_proposed", "false_flags") if key in result}
    return f"{result['case']} #{result['run']}: {mark} {json.dumps(details, ensure_ascii=False)}"


async def main_async(cases: list[Case], runs: int, parallel: int, folder: Path) -> list[dict[str, Any]]:
    limit = asyncio.Semaphore(parallel)
    jobs = [run_one(case, number, folder, limit) for case in cases for number in range(1, runs + 1)]
    return list(await asyncio.gather(*jobs))


def main() -> int:
    parser = argparse.ArgumentParser(prog="nazar_agent.evals", description=__doc__)
    parser.add_argument("--case", choices=sorted(CASES), action="append", help="only these cases; repeatable")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--parallel", type=int, default=3)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()

    cases = [CASES[name] for name in (args.case or CASES)]
    folder = args.out / datetime.now().strftime("%Y-%m-%d-%H%M%S")
    folder.mkdir(parents=True, exist_ok=True)
    results = asyncio.run(main_async(cases, args.runs, args.parallel, folder))
    (folder / "results.json").write_text(json.dumps(results, ensure_ascii=False, indent=1), encoding="utf-8")
    table = summarize(results)
    (folder / "summary.md").write_text(table + "\n", encoding="utf-8")
    print()
    print(table)
    print(f"\nSaved to {folder}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
