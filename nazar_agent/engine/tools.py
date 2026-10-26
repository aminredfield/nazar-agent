"""Tools an agent role may call.

A tool is a typed function: parameters are a pydantic model, so the model's
arguments are validated in our code before anything runs — the API's own
schema check is not trusted on its own (https://dev.meta.ai/docs/tool-calling).

The JSON schema sent to the model is deliberately plain: no extra properties,
only fields without a default are required, and no numeric or format
keywords. Ranges and formats are enforced by pydantic and stated in the field
description instead.

Optional fields are optional, not "required but nullable": tried live on Muse
Spark 1.3, a required nullable field came back as the string "null" or was
left out, and the model worked around it with one call per employee.
``ToolParams`` also reads a stray "null" string as a missing value.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator

ToolHandler = Callable[[Any], Awaitable[Any]]

# Keywords kept out of the schema sent to the model (see module docstring).
_DROPPED_KEYWORDS = frozenset(
    {
        "title",
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
_NULL_STRINGS = frozenset({"null", "none", ""})


class ToolParams(BaseModel):
    """Base for tool parameters: unknown fields are an error, "null" means None."""

    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _null_strings_to_none(cls, data: Any) -> Any:
        if not isinstance(data, dict):
            return data
        return {
            key: None if isinstance(value, str) and value.strip().lower() in _NULL_STRINGS else value
            for key, value in data.items()
        }


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
            "parameters": tool_schema(self.params),
        }


def tool_schema(model: type[BaseModel]) -> dict[str, Any]:
    return _clean_node(model.model_json_schema())


def _clean_node(node: Any) -> Any:
    if isinstance(node, list):
        return [_clean_node(item) for item in node]
    if not isinstance(node, dict):
        return node

    cleaned = {
        key: _clean_node(value)
        for key, value in node.items()
        if key not in _DROPPED_KEYWORDS
    }
    if "properties" in node:
        # ``properties`` maps names to schemas; its keys are field names and
        # must survive even when a field is called e.g. "title".
        cleaned["properties"] = {
            name: _clean_node(schema) for name, schema in node["properties"].items()
        }
    if "$defs" in node:
        cleaned["$defs"] = {name: _clean_node(schema) for name, schema in node["$defs"].items()}
    if cleaned.get("type") == "object":
        cleaned["additionalProperties"] = False
        cleaned["required"] = list(node.get("required", []))
    return cleaned
