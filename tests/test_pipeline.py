import json

import pytest
from pydantic import BaseModel

from nazar_agent.engine import Role, Tool
from nazar_agent.findings import ReportError, analyze
from nazar_agent.findings.pipeline import REPORT_FORMAT
from tests.fakes import ScriptedLLM, call, turn


class NoParams(BaseModel):
    pass


async def events(params: NoParams) -> dict:
    return {"events": [{"id": "ev-1", "employee_id": "emp-1", "distance_to_site_m": 6600.0}]}


ROLE = Role(
    name="analyst",
    instructions="Analyse.",
    tools=(Tool("get_events", "Events.", NoParams, events),),
    model="m",
)


def report_json(distance: str, *, alternatives=("weak GPS",)) -> str:
    return json.dumps({
        "summary": "One check-in is worth a look.",
        "data_gaps": [],
        "findings": [{
            "title": "Far from the site",
            "subject_kind": "employee",
            "concern": "people",
            "subject_id": "emp-1",
            "subject_name": "Jasur",
            "facts": [
                {
                    "text": "Checked in far from the site",
                    "evidence": [{"kind": "event", "id": "ev-1"}],
                    "claims": [{"ref": {"kind": "event", "id": "ev-1"}, "field": "distance_to_site_m", "value": distance}],
                },
                {"text": "Has an employee record", "evidence": [{"kind": "employee", "id": "emp-1"}], "claims": []},
            ],
            "hypothesis": "Was not on site.",
            "alternatives": list(alternatives),
            "confidence": "medium",
        }],
    })


def investigation():
    return [turn(call("get_events", {}), response_id="r1"), turn(text="Seen enough.", response_id="r2")]


async def test_clean_report_passes_first_time_with_structured_output():
    llm = ScriptedLLM([*investigation(), turn(text=report_json("6600"), response_id="r3")])

    analysis = await analyze(ROLE, llm, "Anything odd?")

    report_request = llm.requests[2]
    assert report_request["text_format"] == REPORT_FORMAT
    assert report_request["tools"] == []
    assert report_request["input"][-2] == {"role": "assistant", "content": "Seen enough."}
    assert report_request["input"][-1]["content"].startswith("Now write your report")
    assert analysis.clean_first_time is True
    assert analysis.corrections == 0
    assert analysis.rejected == ()
    assert analysis.caught == ()
    assert len(analysis.report.findings[0].facts) == 2
    assert analysis.input_tokens == 30  # three scripted turns of 10


async def test_checker_sends_problems_back_and_accepts_the_fix():
    llm = ScriptedLLM([
        *investigation(),
        turn(text=report_json("12000"), response_id="r3"),
        turn(text=report_json("6600"), response_id="r4"),
    ])

    analysis = await analyze(ROLE, llm, "Anything odd?")

    correction = llm.requests[3]["input"]
    assert correction[-2]["role"] == "assistant"  # the report that was sent back
    assert "is 6600.0, not '12000'" in correction[-1]["content"]
    assert analysis.corrections == 1
    assert analysis.clean_first_time is False
    assert analysis.rejected == ()
    [[caught]] = analysis.caught
    assert caught.startswith("finding 1, fact 1:")


async def test_what_still_fails_is_removed_and_kept_with_reasons():
    wrong = report_json("12000")
    llm = ScriptedLLM([*investigation(), *(turn(text=wrong) for _ in range(3))])

    analysis = await analyze(ROLE, llm, "Anything odd?", max_corrections=2)

    assert analysis.corrections == 2
    [finding] = analysis.report.findings
    assert [fact.text for fact in finding.facts] == ["Has an employee record"]
    [rejected] = analysis.rejected
    assert rejected.fact == "Checked in far from the site"
    assert "not '12000'" in rejected.reasons[0]


async def test_finding_without_innocent_explanation_is_rejected_whole():
    lonely = report_json("6600", alternatives=())
    llm = ScriptedLLM([*investigation(), *(turn(text=lonely) for _ in range(2))])

    analysis = await analyze(ROLE, llm, "Anything odd?", max_corrections=1)

    assert analysis.report.findings == []
    assert analysis.rejected[0].fact is None


async def test_invalid_json_is_retried_then_fails_loudly():
    llm = ScriptedLLM([*investigation(), turn(text="{oops"), turn(text="{still bad")])

    with pytest.raises(ReportError):
        await analyze(ROLE, llm, "Anything odd?", max_corrections=1)
    assert "does not match the schema" in llm.requests[3]["input"][-1]["content"]


def test_report_schema_fits_strict_mode():
    schema = json.dumps(REPORT_FORMAT["schema"])

    assert "allOf" not in schema and "oneOf" not in schema
    assert '"additionalProperties": true' not in schema


def empty_report(summary: str) -> str:
    return json.dumps({"summary": summary, "data_gaps": [], "findings": []})


async def test_an_empty_report_after_an_investigation_is_asked_for_once_more():
    llm = ScriptedLLM([
        *investigation(),
        turn(text=empty_report("Placeholder"), response_id="r3"),
        turn(text=report_json("6600"), response_id="r4"),
    ])

    analysis = await analyze(ROLE, llm, "Anything odd?")

    assert llm.requests[3]["input"][-1]["content"].startswith("Your report has no findings")
    assert len(analysis.report.findings) == 1
    assert analysis.caught == (("the report had no findings after an investigation; asked once more",),)


async def test_a_second_empty_report_stands():
    llm = ScriptedLLM([
        *investigation(),
        turn(text=empty_report("x"), response_id="r3"),
        turn(text=empty_report("Nothing is worth checking this week."), response_id="r4"),
    ])

    analysis = await analyze(ROLE, llm, "Anything odd?")

    assert analysis.report.findings == []
    assert analysis.report.summary == "Nothing is worth checking this week."
    assert len(llm.requests) == 4
