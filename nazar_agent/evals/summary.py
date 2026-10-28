"""The results table for the README: one row per case, over all its runs."""

from __future__ import annotations

from collections import defaultdict
from typing import Any

from nazar_agent.evals.cases import DEAD_SOULS

TITLES = {
    "dead_souls": "4 «dead souls» checked in from the foreman's phone",
    "wrong_schedule": "Wrong site schedule (08:00 instead of 09:00)",
    "new_crew_list": "New crew list with a duplicate, a repeat and a surname only",
}


def summarize(results: list[dict[str, Any]]) -> str:
    by_case: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for result in results:
        by_case[result["case"]].append(result)
    lines = [
        "| Case | Runs | Passed | Found | Falsely accused | Fact checker clean first time | Actions valid | Avg cost |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for case, runs in by_case.items():
        done = [run for run in runs if "error" not in run]
        lines.append(
            "| "
            + " | ".join(
                [
                    TITLES.get(case, case),
                    str(len(runs)),
                    f"{sum(bool(run['passed']) for run in runs)}/{len(runs)}",
                    _found(case, done),
                    _accused(done),
                    _ratio(sum(bool(run.get("clean_first_time")) for run in done), _count(done, "clean_first_time")),
                    _ratio(sum(run.get("actions_valid", 0) for run in done), sum(run.get("actions_valid", 0) + run.get("actions_rejected", 0) for run in done)),
                    _cost(done),
                ]
            )
            + " |"
        )
    errors = [run for run in results if "error" in run]
    if errors:
        lines.append("")
        lines.extend(f"- {run['case']} #{run['run']} failed: {run['error']}" for run in errors)
    return "\n".join(lines)


def _found(case: str, runs: list[dict[str, Any]]) -> str:
    if not runs:
        return "—"
    if case == "dead_souls":
        return f"{sum(len(run['found']) for run in runs)}/{len(DEAD_SOULS) * len(runs)} people"
    if case == "wrong_schedule":
        fixed = sum(bool(run["schedule_fix_proposed"]) for run in runs)
        return f"schedule {sum(bool(run['schedule_found']) for run in runs)}/{len(runs)}, fix proposed {fixed}/{len(runs)}"
    if case == "new_crew_list":
        caught = sum(len(run["caught"]) for run in runs)
        return f"{caught}/{caught + sum(len(run['missed']) for run in runs)} flags, {sum(len(run['false_flags']) for run in runs)} false"
    return "—"


def _accused(runs: list[dict[str, Any]]) -> str:
    scored = [run for run in runs if "falsely_accused" in run]
    if not scored:
        return "—"
    return f"{sum(len(run['falsely_accused']) for run in scored)} in {len(scored)} runs"


def _count(runs: list[dict[str, Any]], key: str) -> int:
    return sum(key in run for run in runs)


def _ratio(part: int, whole: int) -> str:
    return f"{part}/{whole}" if whole else "—"


def _cost(runs: list[dict[str, Any]]) -> str:
    costs = [run["cost_usd"] for run in runs if "cost_usd" in run]
    return f"${sum(costs) / len(costs):.3f}" if costs else "—"
