"""A name that fits several people, or no one, is asked back before anyone investigates."""


import httpx
import openai
import pytest

from nazar_agent.actions import ProposalStore
from nazar_agent.assistant import Assistant, AssistantBusy
from nazar_agent.clarify import Clarification, resolve
from nazar_agent.config import Settings
from nazar_agent.engine import ToolExecutionError
from nazar_agent.errors import BUSY, MODEL_SLOW, NAZAR_DOWN, NAZAR_FORBIDDEN, UNKNOWN, owner_message
from nazar_agent.evals.questions import BANK_NOW
from nazar_agent.nazar import NazarApiError
from nazar_agent.nazar.client import SESSION_EXPIRED
from nazar_agent.nazar.connect import connect
from nazar_agent.replies import clarification, reply, suggestions
from nazar_agent.roles.dispatcher import Request
from tests.fakes import ScriptedLLM, turn
from tests.test_dispatcher import reading


def settings(tmp_path) -> Settings:
    return Settings(_env_file=None, model_api_key="k", demo_mode=True, demo_now=BANK_NOW, agent_db_path=str(tmp_path / "a.sqlite3"))


@pytest.fixture
async def nazar(tmp_path):
    client = connect(settings(tmp_path)).client()
    yield client
    await client.aclose()


def asked(*people: str) -> Request:
    return Request(intent="data", analyst="timesheet", lang="ru", period=None, people=people)


async def test_two_karimovs_are_asked_back_with_a_button_each(nazar):
    found = await resolve(asked("Каримовым"), "Что с Каримовым?", nazar)

    assert isinstance(found, Clarification) and found.found
    assert {choice.label for choice in found.choices} == {"Бобур Каримов", "Феруза Каримова"}
    assert all(choice.question.startswith("Что с Каримовым? — ") for choice in found.choices)
    text = clarification(found, "ru")
    assert "Бобур Каримов" in text and "Феруза Каримова" in text


async def test_a_pressed_choice_is_not_asked_again(nazar):
    request = asked("Каримовым", "Бобур Каримов")

    assert await resolve(request, "Что с Каримовым? — Бобур Каримов", nazar) is request


async def test_one_match_is_handed_to_the_analyst_with_its_id(nazar):
    found = await resolve(asked("Rustam aka"), "Rustam aka bugun soat nechada keldi?", nazar)

    assert isinstance(found, Request)
    [line] = found.resolved
    assert line.startswith("Rustam aka → Рустам Юлдашев (employee id ") and "found in NAZAR" in found.hints()


async def test_no_one_by_that_name_is_said_at_once(nazar):
    found = await resolve(asked("Алишер Усманов"), "Как работает Алишер Усманов?", nazar)

    assert isinstance(found, Clarification) and not found.found
    assert clarification(found, "ru").startswith("Не нашёл сотрудника «Алишер Усманов»")


async def test_a_site_taken_for_a_person_is_left_to_the_analyst(nazar):
    request = asked("Яшнабад")

    assert await resolve(request, "Кто опоздал на Яшнабаде?", nazar) is request


async def test_an_unclear_name_goes_to_the_analyst_when_nazar_does_not_answer(nazar):
    async def down(*args, **kwargs):
        raise httpx.ConnectError("refused")

    nazar.get = down
    request = asked("Каримовым")

    assert await resolve(request, "Что с Каримовым?", nazar) is request


async def test_the_assistant_asks_back_without_the_analyst(tmp_path, monkeypatch):
    llm = ScriptedLLM([turn(text=reading(people=["Каримовым"]).model_dump_json())])  # one turn: the analyst would fail
    monkeypatch.setattr(Settings, "analyst_llm", lambda self: llm)
    monkeypatch.setattr(Settings, "skeptic_llm", lambda self: ScriptedLLM([]))
    current = settings(tmp_path)
    link = connect(current)
    assistant = Assistant(current, ProposalStore(tmp_path / "a.sqlite3"), service=link.client())

    answer = await assistant.ask("Что с Каримовым?")

    assert answer.view["reply"] == "clarify" and answer.view["cards"] == []
    assert len(answer.view["choices"]) == 2 and "Феруза Каримова" in answer.view["summary"]


@pytest.mark.parametrize("intent", ["greeting", "unclear", "off_topic"])
def test_a_short_answer_offers_questions_to_press(intent):
    request = Request(intent=intent, analyst="timesheet", lang="uz", period=None)

    assert [choice.question for choice in suggestions(request)][0] == "Bugun kim kechikdi?" and reply(request)


def test_a_refusal_offers_nothing_to_press():
    assert suggestions(Request(intent="secrets", analyst="timesheet", lang="ru", period=None)) == ()


@pytest.mark.parametrize(("error", "said"), [
    (AssistantBusy(), BUSY),
    (openai.APITimeoutError(request=httpx.Request("POST", "http://m")), MODEL_SLOW),
    (NazarApiError(401, "expired"), SESSION_EXPIRED),
    (NazarApiError(403, "no"), NAZAR_FORBIDDEN),
    (NazarApiError(502, "bad gateway"), NAZAR_DOWN),
    (httpx.ConnectTimeout("slow"), NAZAR_DOWN),
    (ValueError("boom"), UNKNOWN),
])
def test_a_failure_is_said_in_words(error, said):
    assert owner_message(error) == said


def test_a_nazar_refusal_inside_a_tool_keeps_nazar_s_words():
    try:
        raise ToolExecutionError("NAZAR API error 422: bad date") from NazarApiError(422, "Неверная дата")
    except ToolExecutionError as exc:
        assert owner_message(exc) == "NAZAR отказал в запросе: Неверная дата"




@pytest.mark.parametrize(("lang", "start"), [("ru", "NAZAR отказал"), ("uz", "NAZAR so'rovni rad etdi"), ("en", "NAZAR refused")])
def test_a_failure_is_said_in_the_windows_language(lang, start):
    said = owner_message(NazarApiError(422, "bad date"), lang)

    assert said.startswith(start) and said.endswith("bad date")
    assert owner_message(AssistantBusy(), lang) != owner_message(ValueError("boom"), lang)
