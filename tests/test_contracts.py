"""NAZAR's real answers, recorded (scripts/record_nazar_contracts.py), and the shape check the demo NAZAR must pass."""

import json
from pathlib import Path

import pytest

from nazar_agent.demo.contracts import ENDPOINTS, missing, redact, shape

CONTRACTS = Path(__file__).parent / "contracts"
RECORDED = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(CONTRACTS.glob("*.json"))}


def test_every_route_the_agent_calls_is_recorded():
    recorded = {record["request"] for record in RECORDED.values() if record["status"] < 400}

    assert ENDPOINTS - recorded == set()


def test_nazar_errors_are_recorded_as_the_agent_reads_them():
    for name in ("error_404", "error_422"):
        body = RECORDED[name]["body"]
        assert body["success"] is False and body["error"]["message"]


@pytest.mark.parametrize("name", [name for name, record in RECORDED.items() if "body" in record and record["status"] < 400])
def test_a_recorded_answer_is_an_envelope_with_data(name):
    body = RECORDED[name]["body"]

    assert body["success"] is True and "data" in body


def test_recordings_hold_no_credentials():
    text = "\n".join(path.read_text(encoding="utf-8") for path in CONTRACTS.glob("*.json"))

    assert "eyJ" not in text and "@" not in text


def test_a_missing_field_or_another_type_is_found():
    real = shape({"id": "a", "flags": ["x"], "previous": {"at": "t", "km": 1.5}})

    assert missing(real, shape({"id": "b", "flags": [], "previous": {"at": "t", "km": 2}})) == []
    assert missing(real, shape({"id": "b", "flags": ["y"]})) == ["/previous: missing"]
    assert missing(real, shape({"id": 1, "flags": ["y"], "previous": {"at": "t", "km": 1}})) == ["/id: string expected, got number"]


def test_an_empty_value_says_nothing_about_its_type():
    real = shape([{"comment": None, "photo_url": "x"}, {"comment": "seen", "photo_url": None}])

    assert missing(real, shape([{"comment": None, "photo_url": None}])) == []
    assert missing(real, shape([{"comment": 3, "photo_url": "y"}])) == ["[]/comment: string expected, got number"]


def test_redaction_keeps_types_and_drops_secrets():
    clean = redact({"access_token": "abc", "email": "a@b.c", "url": "https://x/#handoff=eyJhbG", "count": 3})

    assert clean == {"access_token": "<redacted>", "email": "<redacted>", "url": "https://x/#handoff=<redacted>", "count": 3}
