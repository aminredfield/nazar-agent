from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from nazar_agent.engine import strict_schema


class Inner(BaseModel):
    title: str = Field(description="A field literally called title.")
    level: Literal["low", "high"]


class Params(BaseModel):
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


def test_every_object_is_closed_and_fully_required():
    schema = strict_schema(Params)

    objects = [node for node in walk(schema) if node.get("type") == "object"]
    assert len(objects) == 2
    for node in objects:
        assert node["additionalProperties"] is False
        assert sorted(node["required"]) == sorted(node["properties"])


def test_constraint_keywords_are_dropped_but_descriptions_kept():
    schema = strict_schema(Params)

    keys = {key for node in walk(schema) for key in node if key not in NAME_MAPS}
    assert not keys & {"title", "default", "format", "minimum", "maximum", "maxLength"}
    assert schema["properties"]["limit"]["description"] == "1–200."


def test_field_named_title_survives():
    inner = strict_schema(Params)["$defs"]["Inner"]

    assert "title" in inner["properties"]
    assert inner["properties"]["level"]["enum"] == ["low", "high"]


def test_optional_field_stays_nullable():
    note = strict_schema(Params)["properties"]["note"]

    assert {"type": "null"} in note["anyOf"]
