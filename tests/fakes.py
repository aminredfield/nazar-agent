"""A scripted stand-in for the model: returns prepared turns and records requests."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from nazar_agent.llm import ModelTurn, ToolCall


def call(name: str, arguments: dict[str, Any] | str, call_id: str | None = None) -> ToolCall:
    raw = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return ToolCall(call_id=call_id or f"call_{name}", name=name, arguments=raw)


def turn(*calls: ToolCall, text: str = "", response_id: str = "resp") -> ModelTurn:
    return ModelTurn(response_id=response_id, text=text, tool_calls=calls, input_tokens=10, output_tokens=5)


@dataclass
class ScriptedLLM:
    turns: list[ModelTurn]
    requests: list[dict[str, Any]] = field(default_factory=list)

    async def respond(self, **request: Any) -> ModelTurn:
        self.requests.append(request)
        if not self.turns:
            raise AssertionError("The model was called more times than scripted.")
        return self.turns.pop(0)
