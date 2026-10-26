"""Ask the attendance analyst a question from the terminal.

    nazar-agent "Who did not show up on the Chilonzor site yesterday?"
    nazar-agent --trace "..."    # also print every tool call
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime

from nazar_agent.config import get_settings
from nazar_agent.engine import RoleRun, run_role
from nazar_agent.llm import MetaResponsesClient
from nazar_agent.nazar import NazarClient
from nazar_agent.roles import attendance_analyst
from nazar_agent.tools import attendance_tools
from nazar_agent.tools.attendance import COMPANY_TZ


async def ask(question: str) -> RoleRun:
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
    try:
        role = attendance_analyst(
            model=settings.analyst_model,
            tools=attendance_tools(nazar),
            today=datetime.now(COMPANY_TZ).date(),
        )
        return await run_role(role, llm, question)
    finally:
        await nazar.aclose()
        await llm.aclose()


def _print_trace(run: RoleRun) -> None:
    for step in run.steps:
        status = f"error: {step.error}" if step.error else "ok"
        arguments = json.dumps(step.arguments, ensure_ascii=False, default=str)
        print(f"  → {step.name}({arguments}) [{status}, {step.duration_ms} ms]")
    print(
        f"  {run.model_turns} model turns, {run.input_tokens} input / "
        f"{run.output_tokens} output tokens, stopped: {run.stopped}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(prog="nazar-agent", description=__doc__)
    parser.add_argument("question")
    parser.add_argument("--trace", action="store_true", help="print every tool call")
    args = parser.parse_args()

    run = asyncio.run(ask(args.question))
    if args.trace:
        _print_trace(run)
    if run.stopped == "max_steps":
        print("The agent ran out of steps before answering.", file=sys.stderr)
        return 1
    print(run.answer)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
