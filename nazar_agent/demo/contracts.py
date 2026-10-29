"""The shape of NAZAR's answers, to keep the demo NAZAR honest.

``scripts/record_nazar_contracts.py`` saves one real answer of every endpoint
the agent calls (``tests/contracts/*.json``). ``shape`` reduces an answer to
its fields and their types; ``missing`` lists what the demo NAZAR leaves out
or answers with another type. A demo that drifts from the real API fails the
contract tests instead of failing a judge.
"""

from __future__ import annotations

from typing import Any

Shape = Any  # a type name, {"object": {...}}, {"list": shape} or a sorted list of alternatives

SECRET_KEYS = ("token", "password", "secret", "email", "key")

# Every NAZAR route the agent calls (recorded in tests/contracts/). A new call
# in the agent needs a new recording, or the demo NAZAR will not know it.
ENDPOINTS = frozenset({
    "POST /auth/login", "POST /auth/refresh",
    "POST /ai-agent/service-session", "POST /ai-agent/handoff", "POST /ai-agent/session", "POST /ai-agent/usage",
    "GET /objects", "GET /objects/{id}/assignments", "POST /objects/{id}/assignments", "PATCH /objects/{id}",
    "GET /employees", "POST /employees", "POST /face/quality-check",
    "GET /attendance/logs", "GET /attendance/events/{id}", "POST /attendance/events/{id}/review", "GET /attendance/presence",
    "GET /timesheet", "GET /timesheet/day/detail", "GET /timesheet/setup-check",
    "POST /timesheet/adjustments/preview", "POST /timesheet/adjustments", "DELETE /timesheet/adjustments/{id}",
    "POST /timesheet/adjustments/void", "DELETE /timesheet/adjustments/void/{id}",
    "GET /media/{path}",
    # Setting up through the chat.
    "POST /objects", "DELETE /objects/{id}", "POST /objects/{id}/schedule-preview",
    "GET /objects/{id}/groups", "POST /objects/{id}/groups", "DELETE /objects/{id}/groups/{id}",
    "GET /departments", "POST /departments", "DELETE /departments/{id}",
    "POST /departments/bulk-add", "POST /departments/bulk-remove",
})
SAMPLE = 5  # list items looked at to learn an item's shape


def shape(value: Any) -> Shape:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int | float):
        return "number"
    if isinstance(value, str):
        return "string"
    if isinstance(value, list):
        items = [shape(item) for item in value[:SAMPLE]]
        return {"list": _merge_all(items) if items else "any"}
    if isinstance(value, dict):
        return {"object": {key: shape(item) for key, item in value.items()}}
    return type(value).__name__


def _merge_all(shapes: list[Shape]) -> Shape:
    merged = shapes[0]
    for other in shapes[1:]:
        merged = _merge(merged, other)
    return merged


def _merge(first: Shape, second: Shape) -> Shape:
    if first == second:
        return first
    if isinstance(first, dict) and isinstance(second, dict) and "object" in first and "object" in second:
        keys = first["object"].keys() | second["object"].keys()
        return {"object": {
            key: _merge(first["object"].get(key, "null"), second["object"].get(key, "null")) for key in sorted(keys)
        }}
    if isinstance(first, dict) and isinstance(second, dict) and "list" in first and "list" in second:
        if first["list"] == "any":
            return second
        if second["list"] == "any":
            return first
        return {"list": _merge(first["list"], second["list"])}
    # Different types at one place: keep each alternative once.
    return sorted({_key(item): item for item in [*_as_list(first), *_as_list(second)]}.values(), key=_key)


def _as_list(value: Shape) -> list[Shape]:
    return value if isinstance(value, list) else [value]


def _key(value: Shape) -> str:
    return value if isinstance(value, str) else repr(value)


def missing(real: Shape, demo: Shape, path: str = "") -> list[str]:
    """What the demo answer lacks or answers with another type, as readable paths."""
    if real == "null" or demo == "null" or real == "any" or demo == "any":
        return []  # a value that may be empty says nothing about its type
    if isinstance(real, list) or isinstance(demo, list):
        real_options = [option for option in _as_list(real) if option != "null"]
        demo_options = [option for option in _as_list(demo) if option != "null"]
        if not real_options or not demo_options:
            return []
        # Any real alternative that some demo alternative satisfies is enough.
        if any(not missing(real_option, demo_option, path) for real_option in real_options for demo_option in demo_options):
            return []
        return [f"{path or '/'}: {_label(real_options)} expected, got {_label(demo_options)}"]
    if isinstance(real, dict) and "object" in real:
        if not (isinstance(demo, dict) and "object" in demo):
            return [f"{path or '/'}: object expected, got {demo!r}"]
        problems = []
        for key, real_value in real["object"].items():
            if key not in demo["object"]:
                problems.append(f"{path}/{key}: missing")
            else:
                problems.extend(missing(real_value, demo["object"][key], f"{path}/{key}"))
        return problems
    if isinstance(real, dict) and "list" in real:
        if not (isinstance(demo, dict) and "list" in demo):
            return [f"{path or '/'}: list expected, got {demo!r}"]
        return missing(real["list"], demo["list"], f"{path}[]")
    return [] if real == demo else [f"{path or '/'}: {real} expected, got {demo}"]


def _label(options: list[Shape]) -> str:
    def one(option: Shape) -> str:
        if isinstance(option, str):
            return option
        return "object" if "object" in option else "list"

    return " or ".join(one(option) for option in options)


def redact(value: Any) -> Any:
    """A recorded answer without credentials or addresses; the types stay.

    Besides fields named like a secret, any signed token in a string goes —
    e.g. the one-minute pass in the assistant's link (``#handoff=eyJ…``).
    """
    if isinstance(value, str) and "eyJ" in value:
        return value.split("eyJ", 1)[0] + "<redacted>"
    if isinstance(value, dict):
        return {
            key: ("<redacted>" if isinstance(item, str) and any(word in key.lower() for word in SECRET_KEYS) else redact(item))
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [redact(item) for item in value]
    return value
