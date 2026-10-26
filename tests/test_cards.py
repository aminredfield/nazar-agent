import json
from datetime import date

import httpx

from nazar_agent.engine import RoleRun
from nazar_agent.findings import Analysis
from nazar_agent.llm import ChatCompletionsClient
from nazar_agent.recommendations import Team, recommend
from nazar_agent.recommendations.composer import ProposedAction
from tests import factories
from tests.fakes import ScriptedLLM, turn


def analysis(*findings) -> Analysis:
    run = RoleRun("analyst", "", factories.STEPS, 2, 0, 0, "answered", ())
    return Analysis(run, factories.report(*findings), (), 0, True, (), 0, 0)


def skeptic_says(*assessments) -> ScriptedLLM:
    return ScriptedLLM([turn(text=json.dumps({"assessments": list(assessments)}))])


def opinion(case, verdict, explanations=("Скачок GPS",)):
    return {"case": case, "verdict": verdict, "reasoning": "r", "innocent_explanations": list(explanations), "what_would_settle_it": ["Спросить"]}


def action(name, **fields):
    blank = {"event_id": None, "employee_id": None, "work_date": None, "status": None, "intervals": None, "comment_or_reason": None}
    return {"action": name, **blank, **fields}


def writer_says(*plans) -> ScriptedLLM:
    return ScriptedLLM([turn(text=json.dumps({"plans": list(plans)}))])


async def test_cards_combine_both_opinions_and_keep_only_valid_actions():
    first = factories.finding(subject_name="Зарина", confidence="medium")
    second = factories.finding(subject_name="Жасур", alternatives=["Скачок GPS", "Другое"])
    team = Team(
        skeptic_llm=skeptic_says(opinion(1, "concern_supported"), opinion(2, "innocent_likely")),
        skeptic_model="open",
        writer_llm=writer_says(
            {"case": 1, "check_steps": ["Посмотреть фото"], "actions": [
                action("adjust_shift", employee_id=factories.EMPLOYEE, work_date=factories.DAY,
                       intervals=[{"start_at": "2026-10-23T09:06:00+05:00", "end_at": "2026-10-23T17:49:00+05:00"}],
                       comment_or_reason="Считать с 09:06"),
                action("void_shift", employee_id=factories.EMPLOYEE, work_date=factories.DAY, comment_or_reason="x"),
                action("review_event", event_id=factories.EVENT, status="insufficient_data", comment_or_reason="third is cut"),
            ], "why": "Табель посчитал приход по второй отметке."},
            {"case": 2, "check_steps": [], "actions": [], "why": "Невиновное объяснение вероятнее."},
        ),
        writer_model="spark",
    )
    nazar, _ = factories.nazar()

    result = await recommend(analysis(first, second), team, nazar, date(2026, 10, 27))

    zarina, jasur = result.cards
    assert (zarina.confidence, jasur.confidence) == ("medium", "low")
    assert [item.name for item in zarina.actions] == ["adjust_shift"]
    assert zarina.actions[0].preview.after["late_minutes"] == 6
    # Policy: not paying a day needs high confidence; the third action is cut (max two).
    assert [item.name for item in zarina.rejected_actions] == ["void_shift"]
    assert zarina.check_steps == ("Посмотреть фото",)
    assert jasur.alternatives == ("Скачок GPS", "Другое")  # skeptic first, no duplicates
    assert jasur.actions_why == "Невиновное объяснение вероятнее."
    assert result.notes == ()
    assert [usage.role for usage in result.usage] == ["skeptic", "writer"]


async def test_without_a_skeptic_cards_still_come_but_capped_and_explained():
    team = Team(ScriptedLLM([turn(text="oops")]), "open", writer_says(), "spark")
    nazar, _ = factories.nazar()

    result = await recommend(analysis(factories.finding()), team, nazar, date(2026, 10, 27))

    [card] = result.cards
    assert card.confidence == "medium" and card.skeptic is None
    assert result.notes[0].startswith("skeptic: the skeptic's answer did not match")


def test_proposal_passes_only_the_fields_its_action_takes():
    proposal = ProposedAction.model_validate(
        action("review_event", event_id="e", status="no_violation", employee_id="leak", comment_or_reason="ok")
    )

    assert proposal.raw_params() == {"event_id": "e", "status": "no_violation", "comment": "ok"}


async def test_chat_completions_client_sends_structured_output_as_response_format():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "chat-1", "object": "chat.completion", "created": 0, "model": "open",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        })

    client = ChatCompletionsClient(
        api_key="k", base_url="http://baseten.test/v1", timeout_seconds=5,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    reply = await client.respond(
        model="open", instructions="Be fair.", input="cases", tools=[],
        text_format={"type": "json_schema", "name": "R", "schema": {"type": "object"}, "strict": True},
    )

    assert sent["messages"] == [{"role": "system", "content": "Be fair."}, {"role": "user", "content": "cases"}]
    assert sent["response_format"] == {"type": "json_schema", "json_schema": {"name": "R", "schema": {"type": "object"}, "strict": True}}
    assert (reply.text, reply.input_tokens, reply.output_tokens) == ("{}", 7, 3)
