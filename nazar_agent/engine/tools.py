"""Tools an agent role may call.

A tool is a typed function: parameters are a pydantic model, so the model's
arguments are validated in our code before anything runs — the API's own
schema check is not trusted on its own (https://dev.meta.ai/docs/tool-calling).

The JSON schema sent to the model is deliberately conservative: strict mode,
every property required (optional ones are nullable), no ``additionalProperties``,
and no numeric or format keywords. Ranges and formats are enforced by pydantic
and stated in the field description instead, so a provider that supports only
the strict subset still accepts the schema.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel

ToolHandler = Callable[[Any], Awaitable[Any]]

# Keywords kept out of the schema sent to the model (see module docstring).
_DROPPED_KEYWORDS = frozenset(
    {
        "title",
        "default",
        "format",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "pattern",
        "minItems",
        "maxItems",
    }
)


@dataclass(frozen=True, slots=True)
class Tool:
    name: str
    description: str
    params: type[BaseModel]
    handler: ToolHandler

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "name": self.name,
            "description": self.description,
            "parameters": strict_schema(self.params),
            "strict": True,
        }


def strict_schema(model: type[BaseModel]) -> dict[str, Any]:
    return _strict_node(model.model_json_schema())


def _strict_node(node: Any) -> Any:
    if isinstance(node, list):
        return [_strict_node(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned = {
        key: _strict_node(value)
        for key, value in node.items()
        if key not in _DROPPED_KEYWORDS
    }
    if "properties" in node:
        # ``properties`` maps names to schemas; its keys are field names and
        # must survive even when a field is called e.g. "title".
        cleaned["properties"] = {
            name: _strict_node(schema) for name, schema in node["properties"].items()
        }
    if "$defs" in node:
        cleaned["$defs"] = {name: _strict_node(schema) for name, schema in node["$defs"].items()}
    if cleaned.get("type") == "object":
        cleaned["additionalProperties"] = False
        cleaned["required"] = list(cleaned.get("properties", {}))
    return cleaned
