"""Model access behind a small interface.

The engine talks to ``LLMClient``, not to a vendor SDK, so tests drive it with a
scripted fake and roles can run on different models (Muse Spark through Meta
Model API, Muse Spark Open on a hosted provider) without touching the engine.

``MetaResponsesClient`` uses the Responses API of Meta Model API, which is
OpenAI-compatible: https://dev.meta.ai/docs/tool-calling
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from openai import AsyncOpenAI


@dataclass(frozen=True, slots=True)
class ToolCall:
    call_id: str
    name: str
    arguments: str  # raw JSON text exactly as the model produced it


@dataclass(frozen=True, slots=True)
class ModelTurn:
    response_id: str
    text: str
    tool_calls: tuple[ToolCall, ...]
    input_tokens: int = 0
    output_tokens: int = 0


class LLMClient(Protocol):
    async def respond(
        self,
        *,
        model: str,
        instructions: str,
        input: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None,
        text_format: dict[str, Any] | None = None,
    ) -> ModelTurn: ...


class MetaResponsesClient:
    def __init__(self, *, api_key: str, base_url: str, timeout_seconds: float) -> None:
        self._client = AsyncOpenAI(api_key=api_key, base_url=base_url, timeout=timeout_seconds)

    async def respond(
        self,
        *,
        model: str,
        instructions: str,
        input: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        previous_response_id: str | None,
        text_format: dict[str, Any] | None = None,
    ) -> ModelTurn:
        request: dict[str, Any] = {"model": model, "instructions": instructions, "input": input}
        # Instructions are not carried over by previous_response_id, so they are
        # sent on every turn of the conversation.
        if tools:
            request["tools"] = tools
        if previous_response_id:
            request["previous_response_id"] = previous_response_id
        if text_format:
            # Structured output: the answer is generated to this JSON schema.
            request["text"] = {"format": text_format}
        response = await self._client.responses.create(**request)

        calls = tuple(
            ToolCall(call_id=item.call_id, name=item.name, arguments=item.arguments)
            for item in response.output
            if item.type == "function_call"
        )
        usage = response.usage
        return ModelTurn(
            response_id=response.id,
            text=response.output_text or "",
            tool_calls=calls,
            input_tokens=usage.input_tokens if usage else 0,
            output_tokens=usage.output_tokens if usage else 0,
        )

    async def aclose(self) -> None:
        await self._client.close()
