import pytest

from nazar_agent.engine import ToolStep
from nazar_agent.findings import AnalystReport, Evidence, check_report, values_match


def step(output, error=None):
    return ToolStep(call_id="c", name="t", arguments={}, output=output, error=error, duration_ms=1)


# What the tools returned in the live run of Oct 27: a check-in 6.6 km from the
# shop, whose previous check-in was 6.6 km away from *it* — not from the shop.
EVENTS = step({
    "events": [
        {"id": "ev-2", "type": "check_in", "employee_id": "emp-1", "employee_name": "Жасур Тошматов",
         "at": "2026-10-22T10:04:11+05:00", "flags": [{"code": "impossible_speed", "meaning": "too fast"}]},
    ],
    "truncated": False,
})
DETAIL = step({
    "id": "ev-2",
    "distance_to_site_m": 6600.0,
    "previous_event": {"id": "ev-1", "at": "2026-10-22T09:06:00+05:00", "distance_between_check_ins_m": 6600.0},
})
FAILED = step(None, error="NAZAR API error 404: not found")


def evidence():
    return Evidence.from_steps((EVENTS, DETAIL, FAILED))


def report(facts, **finding):
    return AnalystReport.model_validate({
        "summary": "s",
        "data_gaps": [],
        "findings": [{
            "title": "Worth checking",
            "subject_kind": "employee",
            "subject_id": "emp-1",
            "subject_name": "Жасур Тошматов",
            "facts": facts,
            "hypothesis": "h",
            "alternatives": ["weak GPS"],
            "confidence": "medium",
            **finding,
        }],
    })


def fact(text, refs=("ev-2",), claims=()):
    return {
        "text": text,
        "evidence": [{"kind": "event", "id": ref} for ref in refs],
        "claims": [
            {"ref": {"kind": "event", "id": ref}, "field": field, "value": value}
            for ref, field, value in claims
        ],
    }


def test_records_are_merged_and_nested_records_indexed():
    found = evidence()

    assert found.records["ev-2"]["distance_to_site_m"] == 6600.0
    assert found.records["ev-2"]["employee_name"] == "Жасур Тошматов"
    assert found.records["ev-1"]["at"] == "2026-10-22T09:06:00+05:00"
    assert "emp-1" in found.seen_ids


def test_true_facts_pass():
    checked = report([
        fact("Checked in 6.6 km from the shop", claims=[("ev-2", "distance_to_site_m", "6600")]),
        fact("At 10:04", claims=[("ev-2", "at", "10:04")]),
        fact("Moved too fast", claims=[("ev-2", "flags", "impossible_speed")]),
    ])

    assert check_report(checked, evidence()) == []


def test_the_live_mistake_is_caught():
    """The model said the previous check-in was 6.6 km from the shop too."""
    wrong = report([
        fact("The previous check-in was also 6.6 km from the shop",
             refs=("ev-1",), claims=[("ev-1", "distance_to_site_m", "6600")]),
    ])

    [issue] = check_report(wrong, evidence())

    assert issue.code == "missing_field"
    assert "distance_to_site_m" in issue.detail


def test_wrong_value_and_unknown_ids_are_reported():
    bad = report([
        fact("12 km away", claims=[("ev-2", "distance_to_site_m", "12000")]),
        fact("Invented event", refs=("ev-404",), claims=[("ev-404", "at", "10:00")]),
        fact("No source", refs=()),
    ])

    codes = [(issue.fact, issue.code) for issue in check_report(bad, evidence())]

    assert codes == [(0, "value_mismatch"), (1, "unknown_ref"), (1, "unknown_record"), (2, "no_evidence")]


def test_finding_needs_known_subject_and_an_innocent_explanation():
    bad = report([fact("ok")], subject_id="emp-404", alternatives=[" "])

    codes = sorted(issue.code for issue in check_report(bad, evidence()))

    assert codes == ["no_alternatives", "unknown_subject"]


def test_company_wide_finding_needs_no_subject_id():
    company = report([fact("ok")], subject_kind="company", subject_id="")

    assert check_report(company, evidence()) == []


@pytest.mark.parametrize(
    ("claimed", "actual", "expected"),
    [
        ("6600", 6600.0, True),
        ("6 600,0", 6600.0, True),
        ("6.6", 6600.0, False),
        ("6590", 6587.3, True),  # a measurement may be rounded
        ("541", 541, True),
        ("540", 541, False),  # a count of minutes may not
        ("541.0", 541, True),
        ("true", True, True),
        ("false", True, False),
        ("null", None, True),
        ("Зарина", "Зарина Хакимова", True),
        ("За", "Зарина Хакимова", False),  # too short to count as part of a name
        ("mock_location", [{"code": "mock_location", "meaning": "fake GPS"}], True),
        ("absent", {"code": "absent"}, True),
        ("abc", 5, False),
    ],
)
def test_values_match(claimed, actual, expected):
    assert values_match(claimed, actual) is expected
