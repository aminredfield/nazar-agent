"""The dispatcher reads a request, code checks its period, and short requests never reach the analyst."""

import json
from datetime import date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from nazar_agent.actions import ProposalStore
from nazar_agent.assistant import Assistant
from nazar_agent.config import Settings
from nazar_agent.evals.checks import language_of
from nazar_agent.nazar import NazarClient
from nazar_agent.replies import CAN_DO, OPENING, reply
from nazar_agent.roles.dispatcher import MAX_PERIOD_DAYS, Reading, Request, checked, parse
from tests.fakes import ScriptedLLM, turn

TODAY = date(2026, 10, 28)  # a Wednesday


def reading(**values) -> Reading:
    base = {"intent": "data", "analyst": "timesheet", "lang": "ru", "date_from": "", "date_to": "", "people": [], "sites": [], "reason": "r"}
    return Reading(**{**base, **values})


async def test_a_reading_becomes_a_request():
    llm = ScriptedLLM([turn(text=reading(analyst="attendance", lang="uz", date_from="2026-10-27", people=["Rustam "]).model_dump_json())])

    request = await parse(llm, "m", "Kecha Rustam qachon keldi?", TODAY)

    assert request == Request(intent="data", analyst="attendance", lang="uz", period=(date(2026, 10, 27),) * 2, people=("Rustam",))
    assert "Wednesday, 2026-10-28" in llm.requests[0]["instructions"]


async def test_an_unreadable_reading_goes_to_the_timesheet_analyst():
    request = await parse(ScriptedLLM([turn(text="not json")]), "m", "?", TODAY)

    assert request.intent == "data" and request.analyst == "timesheet" and request.period is None


@pytest.mark.parametrize(("date_from", "date_to", "period", "clipped"), [
    ("2026-10-27", "2026-10-21", (date(2026, 10, 21), date(2026, 10, 27)), False),  # swapped
    ("2026-10-01", "2026-11-30", (date(2026, 10, 1), TODAY), False),  # not past today
    ("2026-04-28", "2026-10-28", (date(2026, 9, 28), TODAY), True),  # half a year → its last days
    ("вчера", "", None, False),  # not a date
])
def test_code_makes_the_period_sane(date_from, date_to, period, clipped):
    request = checked(reading(date_from=date_from, date_to=date_to), TODAY)

    assert (request.period, request.clipped) == (period, clipped)
    if period:
        assert (period[1] - period[0]).days < MAX_PERIOD_DAYS


def test_a_question_about_the_future_is_not_investigated():
    request = checked(reading(date_from="2026-10-29"), TODAY)

    assert request.intent == "future" and request.short
    assert "29.10.2026" in reply(request)


def test_the_analyst_is_told_what_the_dispatcher_read():
    hints = checked(reading(lang="en", date_from="2026-04-01", date_to="2026-10-28", people=["Rustam"]), TODAY).hints()

    assert "2026-09-28 to 2026-10-28" in hints and "data gaps" in hints and "Rustam" in hints and "English" in hints


@pytest.mark.parametrize("lang", list(CAN_DO))
@pytest.mark.parametrize("intent", list(OPENING))
def test_every_short_answer_is_in_the_owners_language_and_says_what_it_can_do(intent, lang):
    text = reply(Request(intent=intent, analyst="timesheet", lang=lang, period=None))

    assert language_of(text) == lang
    assert any(word in text.lower() for word in ("могу", "can ", "yordam", "ёрдам"))


async def test_a_short_request_never_reaches_the_analyst_or_nazar(tmp_path, monkeypatch):
    llm = ScriptedLLM([turn(text=reading(intent="off_topic").model_dump_json())])  # one turn: the analyst would fail
    monkeypatch.setattr(Settings, "analyst_llm", lambda self: llm)
    monkeypatch.setattr(Settings, "skeptic_llm", lambda self: ScriptedLLM([]))
    now = datetime(2026, 10, 28, 11, 30, tzinfo=ZoneInfo("Asia/Tashkent"))
    settings = Settings(_env_file=None, model_api_key="k", demo_mode=True, demo_now=now, agent_db_path=str(tmp_path / "a.sqlite3"))

    def no_nazar(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"NAZAR was read: {request.url}")

    nazar = NazarClient(base_url="http://nazar.test/api", email="a", password="b", transport=httpx.MockTransport(no_nazar))
    assistant = Assistant(settings, ProposalStore(tmp_path / "a.sqlite3"), service=nazar)

    answer = await assistant.ask("Какая погода в Ташкенте?")

    assert answer.view["reply"] == "off_topic" and answer.view["cards"] == [] and "могу" in answer.view["summary"].lower()
    assert [call["role"] for call in answer.view["stats"]["models"]] == ["dispatcher"]
    assert json.dumps(answer.view, ensure_ascii=False)


@pytest.mark.parametrize(("lang", "question", "expected"), [
    ("uz", "Бугун ким кечикди?", "uz-cyrl"),
    ("uz-cyrl", "Bugun kim kechikdi?", "uz"),
    ("ru", "Kim bugun опоздал?", "ru"),
])
def test_uzbek_is_answered_in_the_letters_it_was_asked_in(lang, question, expected):
    assert checked(reading(lang=lang), TODAY, question).lang == expected


async def test_the_window_language_is_told_and_kept_when_reading_fails():
    llm = ScriptedLLM([turn(text="not json")])

    request = await parse(llm, "m", "Rustam?", TODAY, "en")

    assert request.lang == "en"
    assert "The owner's window is in en" in llm.requests[0]["instructions"]
