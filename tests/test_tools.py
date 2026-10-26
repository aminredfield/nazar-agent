from typing import Literal
from uuid import UUID

import pytest
from pydantic import Field, ValidationError

from nazar_agent.engine import ToolParams, tool_schema


class Inner(ToolParams):
    title: str = Field(description="A field literally called title.")
    level: Literal["low", "high"]


class Params(ToolParams):
    event_id: UUID
    note: str | None = Field(default=None, max_length=50)
    limit: int = Field(default=10, ge=1, le=200, description="1–200.")
    inner: Inner


NAME_MAPS = ("properties", "$defs")  # their keys are names, not schema keywords


def walk(node):
    """Every schema node, skipping the name maps themselves."""
    if isinstance(node, list):
        for item in node:
            yield from walk(item)
        return
    if not isinstance(node, dict):
        return
    yield node
    for key, value in node.items():
        if key in NAME_MAPS:
            for child in value.values():
                yield from walk(child)
        else:
            yield from walk(value)


def test_objects_are_closed_and_only_fields_without_default_are_required():
    schema = tool_schema(Params)

    assert schema["additionalProperties"] is False
    assert sorted(schema["required"]) == ["event_id", "inner"]
    inner = schema["$defs"]["Inner"]
    assert inner["additionalProperties"] is False
    assert sorted(inner["required"]) == ["level", "title"]


def test_constraint_keywords_are_dropped_but_descriptions_kept():
    schema = tool_schema(Params)

    keys = {key for node in walk(schema) for key in node if key not in NAME_MAPS}
    assert not keys & {"title", "format", "minimum", "maximum", "maxLength"}
    assert schema["properties"]["limit"]["description"] == "1–200."


def test_field_named_title_survives():
    inner = tool_schema(Params)["$defs"]["Inner"]

    assert "title" in inner["properties"]
    assert inner["properties"]["level"]["enum"] == ["low", "high"]


def test_null_string_from_the_model_means_missing():
    params = Params.model_validate({
        "event_id": "00000000-0000-0000-0000-000000000001",
        "note": "null",
        "inner": {"title": "t", "level": "low"},
    })

    assert params.note is None


def test_unknown_argument_is_rejected():
    with pytest.raises(ValidationError, match="extra"):
        Params.model_validate({
            "event_id": "00000000-0000-0000-0000-000000000001",
            "inner": {"title": "t", "level": "low"},
            "drop_table": True,
        })
