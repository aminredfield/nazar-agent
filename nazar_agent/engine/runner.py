"""One engine, many roles.

A role is configuration — instructions, the tools it may use and the model it
runs on. The dispatcher, the analysts and the skeptic are all roles run by the
same loop, so adding a role means describing it, not writing a new service.

The loop: send the request → run every tool call the model asked for →
return the results → repeat until the model answers without tool calls or
the step budget runs out. Every tool call is recorded, because the fact
checker later verifies the answer against exactly these outputs.

The conversation is kept here and sent whole on every turn. Chaining turns
on the server with previous_response_id failed live twice on Oct 27
("referenced response not found or expired"); the input tokens are billed
either way.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass
from typing import Any, Literal

from pydantic import ValidationError

from nazar_agent.engine.tools import Tool
from nazar_agent.llm.client import LLMClient, ToolCall

DEFAULT_MAX_STEPS = 8
log = logging.getLogger(__name__)


class ToolExecutionError(Exception):
    """An expected tool failure: its message goes back to the model as the result."""


@dataclass(frozen=True, slots=True)
class Role:
    name: str
    instructions: str
    tools: tuple[Tool, ...]
    model: str
    max_steps: int = DEFAULT_MAX_STEPS


@dataclass(frozen=True, slots=True)
class ToolStep:
    call_id: str
    name: str
    arguments: Any  # parsed JSON when it parsed, otherwise the raw text
    output: Any
    error: str | None
    duration_ms: int


@dataclass(frozen=True, slots=True)
class RoleRun:
    role: str
    answer: str
    steps: tuple[ToolStep, ...]
    model_turns: int
    input_tokens: int
    output_tokens: int
    stopped: Literal["answered", "max_steps"]
    # The whole conversation, so a follow-up (the structured report) continues it.
    conversation: tuple[dict[str, Any], ...]


async def run_role(role: Role, llm: LLMClient, user_input: str) -> RoleRun:
    tools_by_name = {tool.name: tool for tool in role.tools}
    schemas = [tool.schema() for tool in role.tools]

    steps: list[ToolStep] = []
    conversation: list[dict[str, Any]] = [{"role": "user", "content": user_input}]
    input_tokens = output_tokens = 0

    for turn_number in range(1, role.max_steps + 1):
        log.info("%s: turn %d", role.name, turn_number)
        turn = await llm.respond(
            model=role.model,
            instructions=role.instructions,
            input=list(conversation),
            tools=schemas,
        )
        input_tokens += turn.input_tokens
        output_tokens += turn.output_tokens
        if turn.text:
            conversation.append({"role": "assistant", "content": turn.text})

        if not turn.tool_calls:
            return RoleRun(
                role=role.name,
                answer=turn.text,
                steps=tuple(steps),
                model_turns=turn_number,
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                stopped="answered",
                conversation=tuple(conversation),
            )

        turn_steps = await asyncio.gather(
            *(_execute(call, tools_by_name) for call in turn.tool_calls)
        )
        steps.extend(turn_steps)
        conversation.extend(_call_item(call) for call in turn.tool_calls)
        conversation.extend(_call_output(step) for step in turn_steps)

    return RoleRun(
        role=role.name,
        answer="",
        steps=tuple(steps),
        model_turns=role.max_steps,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stopped="max_steps",
        conversation=tuple(conversation),
    )


async def _execute(call: ToolCall, tools_by_name: dict[str, Tool]) -> ToolStep:
    started = time.perf_counter()

    def step(arguments: Any, *, output: Any = None, error: str | None = None) -> ToolStep:
        return ToolStep(
            call_id=call.call_id,
            name=call.name,
            arguments=arguments,
            output=output,
            error=error,
            duration_ms=round((time.perf_counter() - started) * 1000),
        )

    tool = tools_by_name.get(call.name)
    if tool is None:
        return step(call.arguments, error=f"Unknown tool {call.name!r}.")
    try:
        arguments = json.loads(call.arguments or "{}")
    except json.JSONDecodeError as exc:
        return step(call.arguments, error=f"Arguments are not valid JSON: {exc.msg}.")
    try:
        params = tool.params.model_validate(arguments)
    except ValidationError as exc:
        return step(arguments, error=f"Invalid arguments: {_validation_summary(exc)}")
    try:
        output = await tool.handler(params)
    except ToolExecutionError as exc:
        return step(arguments, error=str(exc))
    return step(arguments, output=output)


def _call_item(call: ToolCall) -> dict[str, Any]:
    return {"type": "function_call", "call_id": call.call_id, "name": call.name, "arguments": call.arguments}


def _call_output(step: ToolStep) -> dict[str, Any]:
    payload = {"error": step.error} if step.error is not None else step.output
    return {
        "type": "function_call_output",
        "call_id": step.call_id,
        "output": json.dumps(payload, ensure_ascii=False, default=str),
    }


def _validation_summary(exc: ValidationError) -> str:
    return "; ".join(
        f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
        for error in exc.errors()
    )
