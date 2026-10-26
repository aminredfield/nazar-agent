import json

import pytest

from nazar_agent.recommendations import SkepticView, merge_confidence
from nazar_agent.recommendations.skeptic import SKEPTIC_FORMAT, cases_text, run_skeptic
from tests import factories
from tests.fakes import ScriptedLLM, turn


def view(verdict: str) -> SkepticView:
    return SkepticView(verdict=verdict, reasoning="r", innocent_explanations=(), what_would_settle_it=())


@pytest.mark.parametrize(
    ("analyst", "verdict", "expected"),
    [
        ("high", "concern_supported", "high"),
        ("medium", "concern_supported", "medium"),
        ("high", "inconclusive", "medium"),
        ("low", "inconclusive", "low"),
        ("high", "innocent_likely", "low"),
        ("high", None, "medium"),  # no second opinion — never above medium
        ("low", None, "low"),
    ],
)
def test_confidence_is_set_by_rules_from_both_opinions(analyst, verdict, expected):
    assert merge_confidence(analyst, view(verdict) if verdict else None) == expected


def test_skeptic_sees_facts_and_records_but_not_the_conclusion():
    finding = factories.finding()

    sent = cases_text(factories.report(finding), factories.evidence())

    for concluded in (finding["hypothesis"], finding["title"], finding["alternatives"][0]):
        assert concluded not in sent
    case = json.loads(sent)["cases"][0]
    assert case["facts"][0]["text"] == finding["facts"][0]["text"]
    assert case["facts"][0]["records"][0]["gps_accuracy_m"] == 15.0
    assert '"high"' not in sent  # the analyst's confidence


async def test_opinions_are_matched_to_findings_by_case_number():
    answer = {"assessments": [
        {"case": 2, "verdict": "innocent_likely", "reasoning": "r", "innocent_explanations": ["GPS"], "what_would_settle_it": ["ask"]},
        {"case": 7, "verdict": "concern_supported", "reasoning": "r", "innocent_explanations": [], "what_would_settle_it": []},
    ]}
    llm = ScriptedLLM([turn(text=json.dumps(answer))])

    result = await run_skeptic(llm, "open", factories.report(factories.finding(), factories.finding()), factories.evidence())

    assert set(result.views) == {1}
    assert result.views[1].innocent_explanations == ("GPS",)
    assert result.error == "no opinion on 1 case(s)"
    request = llm.requests[0]
    assert (request["model"], request["text_format"], request["tools"]) == ("open", SKEPTIC_FORMAT, [])


async def test_unreadable_answer_means_no_opinion_with_a_reason():
    llm = ScriptedLLM([turn(text="{not json")])

    result = await run_skeptic(llm, "open", factories.report(factories.finding()), factories.evidence())

    assert result.views == {}
    assert "did not match the schema" in result.error


async def test_nothing_to_doubt_costs_nothing():
    llm = ScriptedLLM([])

    result = await run_skeptic(llm, "open", factories.report(), factories.evidence())

    assert result.views == {} and result.error is None and llm.requests == []
