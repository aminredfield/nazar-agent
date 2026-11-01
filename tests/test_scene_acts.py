"""The Scenarios page's actions: code picks them from the model's verdict, the visitor applies and undoes them,
and the employee's cabinet carries the question and the answer between the two pages."""

import json

import pytest
from fastapi.testclient import TestClient

from nazar_agent.config import Settings
from nazar_agent.scenarios import EXPLAIN, HEADCOUNT, PLACES
from nazar_agent.scenarios.acts import ActRefused, SceneDesk, allowed
from nazar_agent.service import create_app
from tests.fakes import ScriptedLLM, turn


def test_each_verdict_offers_only_its_own_actions():
    assert allowed("gps", "cafe", "not_same") == ("flag_review", "live_selfie")
    assert allowed("gps", "cafe", "same") == ("close_fine",)
    assert allowed("explain", "cafe-late", "consistent") == ("close_fine",)
    assert allowed("explain", "store-screen", "consistent") == ("void_day",)  # he did not come: the day is not paid
    assert allowed("explain", "cleaning-address", "contradicts") == ("flag_review",)
    assert allowed("explain", "cafe-late", "cannot_tell") == ("flag_review",)
    assert allowed("headcount", "cafe", "missing") == ("live_selfie",)
    assert allowed("headcount", "cafe", "all_here") == ("close_fine",)
    assert allowed("headcount", "cafe", "extra") == ("flag_review",)
    assert allowed("roster", "chat", "read") == ()


def test_an_action_is_applied_once_undone_and_applied_again():
    desk = SceneDesk()
    desk.judged("v1", "explain", "store-screen", "consistent")

    applied = desk.act("v1", "explain", "store-screen", "void_day")
    with pytest.raises(ActRefused):
        desk.act("v1", "explain", "store-screen", "void_day")
    undone = desk.act("v1", "explain", "store-screen", "void_day", undo=True)
    again = desk.act("v1", "explain", "store-screen", "void_day")

    assert applied["status"] == "applied" and again["status"] == "applied" and undone["status"] == "undone"
    assert (applied["effect"]["field"], applied["effect"]["before"], applied["effect"]["after"]) == ("paid", 539, 0)
    assert applied["effect"]["pays"] is True


def test_an_action_the_verdict_does_not_allow_is_refused():
    desk = SceneDesk()
    with pytest.raises(ActRefused):
        desk.act("v1", "gps", "cafe", "flag_review")  # nothing judged yet
    desk.judged("v1", "gps", "cafe", "same", subject="0")
    with pytest.raises(ActRefused):
        desk.act("v1", "gps", "cafe", "flag_review")  # the place is the same: only "close" fits
    with pytest.raises(ActRefused):
        desk.act("v1", "gps", "cafe", "fine_everyone")
    with pytest.raises(ActRefused):
        desk.act("v2", "gps", "cafe", "close_fine")  # another visitor's verdict is not theirs


def test_a_new_verdict_starts_the_scene_over():
    desk = SceneDesk()
    desk.judged("v1", "gps", "cafe", "not_same", subject="1")
    desk.act("v1", "gps", "cafe", "flag_review")

    desk.judged("v1", "gps", "cafe", "same", subject="0")
    state = desk.act("v1", "gps", "cafe", "close_fine")

    assert state["effect"]["who"] == PLACES["cafe"]["candidates"][0]["who"]
    assert desk.applied("v1", "gps", "cafe") == ["close_fine"]


def test_a_selfie_request_names_who_is_asked():
    desk = SceneDesk()
    desk.judged("v1", "headcount", "cafe", "missing")

    effect = desk.act("v1", "headcount", "cafe", "live_selfie")["effect"]

    assert effect["field"] == "selfie" and effect["count"] == len(HEADCOUNT["cafe"]["checked_in"])
    assert effect["pays"] is False


def test_the_cabinet_keeps_one_conversation_per_visitor_and_scene():
    desk = SceneDesk()
    desk.asked("v1", "cafe-late", "Hi Lena!", "en")

    assert desk.inbox("v1", "cafe-late")["message"] == "Hi Lena!"
    assert desk.inbox("v2", "cafe-late")["message"] == ""
    desk.forget("v1")
    assert desk.inbox("v1", "cafe-late")["message"] == ""


@pytest.fixture
def window(tmp_path, monkeypatch):
    replies = ScriptedLLM([
        turn(text=json.dumps({"message": "Hi Owen, what happened on Saturday?"})),
        turn(text=json.dumps({"verdict": "consistent", "why": "Maria's phone, a screen.", "next_step": "Do not pay the day."})),
    ])
    monkeypatch.setattr(Settings, "analyst_llm", lambda self: replies)
    settings = Settings(_env_file=None, model_api_key="k", demo_mode=True, agent_db_path=str(tmp_path / "a.sqlite3"))
    with TestClient(create_app(settings)) as client:
        client.get("/api/me")
        yield client


def test_the_question_reaches_the_cabinet_the_answer_comes_back_with_its_actions(window):
    asked = window.post("/api/scenarios/explain/ask", json={"scene": "store-screen", "lang": "en"}).json()
    seen = window.get("/api/scenarios/inbox", params={"scene": "store-screen"}).json()
    replied = window.post("/api/scenarios/inbox/reply", json={"scene": "store-screen", "said": "I didn't come in on Saturday."}).json()
    after = window.get("/api/scenarios/inbox", params={"scene": "store-screen"}).json()
    void = window.post("/api/scenarios/act", json={"page": "explain", "scene": "store-screen", "action": "void_day"}).json()
    refused = window.post("/api/scenarios/act", json={"page": "explain", "scene": "store-screen", "action": "flag_review"})

    assert asked["message"] == seen["message"] == "Hi Owen, what happened on Saturday?"
    assert seen["employee"] == EXPLAIN["store-screen"]["employee"] and seen["reply"] == ""
    assert replied["verdict"] == "consistent" and replied["actions"] == ["void_day"]
    assert after["reply"] == "I didn't come in on Saturday." and after["check"]["verdict"] == "consistent"
    assert void["status"] == "applied" and void["effect"]["after"] == 0
    assert refused.status_code == 409


def test_the_cabinet_answers_only_after_the_question(window):
    early = window.post("/api/scenarios/inbox/reply", json={"scene": "cafe-late", "said": "The bus was late."})
    unknown = window.get("/api/scenarios/inbox", params={"scene": "nope"})
    page = window.get("/scenarios/explain/cabinet")

    assert early.status_code == 409 and unknown.status_code == 404
    assert page.status_code == 200 and page.headers["cache-control"] == "no-cache"


def test_resetting_the_demo_clears_the_visitors_scenes(window):
    window.post("/api/scenarios/explain/ask", json={"scene": "store-screen", "lang": "en"})

    window.post("/api/demo/reset")

    assert window.get("/api/scenarios/inbox", params={"scene": "store-screen"}).json()["message"] == ""
