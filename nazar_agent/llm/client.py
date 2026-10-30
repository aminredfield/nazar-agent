"""Model access behind a small interface.

The engine talks to ``LLMClient``, not to a vendor SDK, so tests drive it with a
scripted fake and roles can run on different models (Muse Spark through Meta
Model API, Muse Spark Open on a hosted provider) without touching the engine.

``MetaResponsesClient`` uses the Responses API of Meta Model API, which is
OpenAI-compatible: https://dev.meta.ai/docs/tool-calling
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

import httpx
from openai import APIConnectionError, APITimeoutError, AsyncOpenAI

log = logging.getLogger(__name__)

# The SDK retries a timed-out request twice by default: a stuck call then
# blocks for three timeouts (seen live: ~8 minutes for one question).
MAX_RETRIES = 1
# A dropped connection (not a slow model: a timeout is not tried again) is
# tried again after these pauses. In the evals a minute-long network blip
# failed 16 questions in a row.
RECONNECT_PAUSES = (3.0, 10.0)


async def _reconnecting(send: Callable[[], Awaitable[Any]]) -> Any:
    for pause in (*RECONNECT_PAUSES, None):
        try:
            return await send()
        except APITimeoutError:
            raise
        except APIConnectionError:
            if pause is None:
                raise
            log.warning("model API connection dropped; trying again in %.0f s", pause)
            await asyncio.sleep(pause)
    raise AssertionError("unreachable")


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
        text_format: dict[str, Any] | None = None,
        max_output_tokens: int | None = None,
        role: str = "",
    ) -> ModelTurn: ...


class MetaResponsesClient:
    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
        efforts: Mapping[str, str] | None = None,
    ) -> None:
        # How long each role thinks (reasoning.effort); a role not in it lets the model choose.
        self._efforts = dict(efforts or {})
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=MAX_RETRIES,
            http_client=http_client,
        )

    async def respond(
        self,
        *,
        model: str,
        instructions: str,
        input: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        text_format: dict[str, Any] | None = None,
        max_output_tokens: int | None = None,
        role: str = "",
    ) -> ModelTurn:
        request: dict[str, Any] = {"model": model, "instructions": instructions, "input": input}
        if tools:
            request["tools"] = tools
        if text_format:
            # Structured output: the answer is generated to this JSON schema.
            request["text"] = {"format": text_format}
        if max_output_tokens:
            request["max_output_tokens"] = max_output_tokens
        if role in self._efforts:
            request["reasoning"] = {"effort": self._efforts[role]}
        response = await _reconnecting(lambda: self._client.responses.create(**request))

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


class ChatCompletionsClient:
    """A model behind the Chat Completions API — e.g. Muse Spark Open on Baseten.

    Used for single-shot roles (the skeptic, the photo inspector): one request,
    no tools, no server-side conversation. Structured output maps to
    ``response_format``. Input is text, or Responses-style messages with
    ``input_text`` and ``input_image`` parts, sent as chat ``text`` and
    ``image_url`` parts — so a role can move between the two APIs unchanged.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        timeout_seconds: float,
        http_client: httpx.AsyncClient | None = None,
        efforts: Mapping[str, str] | None = None,
    ) -> None:
        # How long each role thinks (reasoning.effort); a role not in it lets the model choose.
        self._efforts = dict(efforts or {})
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            timeout=timeout_seconds,
            max_retries=MAX_RETRIES,
            http_client=http_client,
        )

    async def respond(
        self,
        *,
        model: str,
        instructions: str,
        input: str | list[dict[str, Any]],
        tools: list[dict[str, Any]],
        text_format: dict[str, Any] | None = None,
        max_output_tokens: int | None = None,
        role: str = "",
    ) -> ModelTurn:
        if tools:
            raise NotImplementedError("ChatCompletionsClient serves single-shot roles without tools.")

        messages = [{"role": "user", "content": input}] if isinstance(input, str) else [_chat_message(item) for item in input]
        request: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": instructions}, *messages],
        }
        if text_format:
            request["response_format"] = {
                "type": "json_schema",
                "json_schema": {
                    "name": text_format["name"],
                    "schema": text_format["schema"],
                    "strict": text_format.get("strict", False),
                },
            }
        if max_output_tokens:
            request["max_tokens"] = max_output_tokens
        if role in self._efforts:
            request["reasoning_effort"] = self._efforts[role]
        response = await _reconnecting(lambda: self._client.chat.completions.create(**request))

        usage = response.usage
        return ModelTurn(
            response_id=response.id,
            text=response.choices[0].message.content or "",
            tool_calls=(),
            input_tokens=usage.prompt_tokens if usage else 0,
            output_tokens=usage.completion_tokens if usage else 0,
        )

    async def aclose(self) -> None:
        await self._client.close()


def _chat_message(message: dict[str, Any]) -> dict[str, Any]:
    content = message["content"]
    if isinstance(content, str):
        return {"role": message["role"], "content": content}
    return {"role": message["role"], "content": [_chat_part(part) for part in content]}


def _chat_part(part: dict[str, Any]) -> dict[str, Any]:
    if part["type"] == "input_text":
        return {"type": "text", "text": part["text"]}
    if part["type"] == "input_image":
        return {"type": "image_url", "image_url": {"url": part["image_url"]}}
    raise ValueError(f"Unsupported input part: {part['type']}")
