"""A plan of changes applied in the demo NAZAR step by step, with the audit trail, and undone in reverse."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from nazar_agent.config import Settings
from nazar_agent.engine import ToolExecutionError
from nazar_agent.service import create_app
from nazar_agent.setup import apply as applying
from tests.fakes import ScriptedLLM, turn
from tests.test_dispatcher import reading as dispatcher_reading
from tests.test_setup_plan import OLMAZOR, OPEN_OLMAZOR

NOW = datetime(2026, 10, 28, 11, 30, tzinfo=ZoneInfo("Asia/Tashkent"))
ASK = f"Открываем ЖК Олмазор {OLMAZOR}, смена с 8 до 17 пн–сб, бригада Рустама: Рустам, Bobur Karimov и Жасур Тоиров"


@pytest.fixture
def window(tmp_path, monkeypatch):
    reading = OPEN_OLMAZOR.model_copy(update={"people": ["Рустам", "Bobur Karimov", "Жасур Тоиров"]})
    script = [turn(text=dispatcher_reading(intent="setup").model_dump_json()), turn(text=reading.model_dump_json())]
    monkeypatch.setattr(Settings, "analyst_llm", lambda self: ScriptedLLM([script.pop(0)]))
    monkeypatch.setattr(Settings, "skeptic_llm", lambda self: ScriptedLLM([]))
    settings = Settings(
        _env_file=None, model_api_key="k", demo_mode=True, demo_now=NOW, require_sign_in=True,
        agent_db_path=str(tmp_path / "agent.sqlite3"),
    )
    app = create_app(settings)
    with TestClient(app) as client:
        client.get("/api/me")  # the demo visitor's session
        assert client.post("/api/ask", json={"question": ASK}).status_code == 200
        yield client, app.state.link.demo


def people(demo, name: str) -> str:
    return next(person.id for person in demo.world.people.values() if person.name == name)


def test_a_plan_is_applied_step_by_step_and_undone_back_to_how_it_was(window):
    client, demo = window
    shown = client.get("/api/setup").json()["setup"]
    draft = shown["draft"]["id"]
    rustam = people(demo, "Rustam Yuldashev")
    home = demo.world.people[rustam].site_id

    client.patch("/api/setup", json={"draft_id": draft, "person": {"written": "Bobur Karimov", "state": "skip"}})
    applied = client.post("/api/setup/apply", json={"draft_id": draft}).json()

    run = applied["run"]
    assert run["status"] == "applied" and all(step["done"] for step in run["steps"]) and applied["setup"] is None
    assert [step["text"][:14] for step in run["steps"]] == [
        "Создам объект ", "Создам группу ", "Поставлю групп", "Добавлю в груп", "Назначу на объ", "Добавлю в «Жду",
    ]
    site = next(item for item in demo.world.sites.values() if item.name == "ЖК Олмазор")
    group = next(item for item in demo.setup.departments.values() if item.name == "Бригада Рустама")
    assert demo.world.sites_of(rustam) == [home, site.id]  # added to his site, not moved
    assert list(group.members) == [rustam] and site.id in demo.setup.groups
    assert [item["full_name"] for item in client.get("/api/import/drafts").json()] == ["Жасур Тоиров"]
    events = [entry["event"] for entry in client.get("/api/audit").json()]
    assert events.count("setup_step") == 6

    undone = client.post(f"/api/setup/runs/{run['id']}/undo", json={}).json()["run"]

    assert undone["status"] == "undone" and all(step["undone"] for step in undone["steps"]) and undone["error"] is None
    assert site.id not in demo.world.sites and group.id not in demo.setup.departments
    assert demo.world.sites_of(rustam) == [home]
    assert client.get("/api/import/drafts").json() == []
    assert client.post(f"/api/setup/runs/{run['id']}/undo", json={}).status_code == 409  # once


def test_a_step_that_fails_stops_the_plan_and_undo_takes_back_what_was_done(window, monkeypatch):
    client, demo = window
    draft = client.get("/api/setup").json()["setup"]["draft"]["id"]
    real = applying._do

    async def refused(kind, params, *rest):
        if kind == "add_to_group":
            raise ToolExecutionError("NAZAR refused: closed period")
        return await real(kind, params, *rest)

    monkeypatch.setattr(applying, "_do", refused)
    failed = client.post("/api/setup/apply", json={"draft_id": draft}).json()

    run = failed["run"]
    assert run["status"] == "failed" and "closed period" in run["error"]
    assert [step["done"] for step in run["steps"]] == [True, True, True, False, False, False]
    assert failed["setup"] is not None  # the draft stays, to fix and try again
    assert client.post("/api/setup/apply", json={"draft_id": draft}).status_code == 409  # not twice

    client.post(f"/api/setup/runs/{run['id']}/undo", json={})

    assert not any(item.name == "ЖК Олмазор" for item in demo.world.sites.values())
    assert sorted(item.name for item in demo.setup.departments.values()) == ["Chilonzor crew", "Sergeli crew", "Yashnobod finishing crew"]


def test_a_pasted_link_puts_the_point_or_says_why_not(window):
    client, _ = window
    draft = client.get("/api/setup").json()["setup"]["draft"]["id"]

    moved = client.post("/api/setup/link", json={"draft_id": draft, "url": "https://www.google.com/maps?q=41.3,69.25"})
    wrong = client.post("/api/setup/link", json={"draft_id": draft, "url": "https://example.com/place"})

    site = moved.json()["setup"]["draft"]["site"]
    assert (site["latitude"], site["longitude"], site["point_source"]) == (41.3, 69.25, "google")
    assert wrong.status_code == 422 and wrong.json()["detail"] == "Это не ссылка на карту — точку из неё не взять."


def test_edits_are_checked_and_an_incomplete_plan_is_not_applied(window):
    client, _ = window
    draft = client.get("/api/setup").json()["setup"]["draft"]["id"]

    stale = client.patch("/api/setup", json={"draft_id": "other", "shift": {"start": "09:00"}})
    unknown = client.patch("/api/setup", json={"draft_id": draft, "site": {"existing_id": "nope"}})
    empty = client.patch("/api/setup", json={"draft_id": draft, "site": {"state": "skip"}})
    refused = client.post("/api/setup/apply", json={"draft_id": draft})

    assert stale.status_code == 422 and unknown.status_code == 422
    assert [item["kind"] for item in empty.json()["setup"]["missing"]] == ["site"]
    assert refused.status_code == 409


def test_without_a_draft_the_window_is_told_to_ask_first(tmp_path):
    settings = Settings(_env_file=None, model_api_key="k", demo_mode=True, demo_now=NOW, agent_db_path=str(tmp_path / "a.sqlite3"))
    with TestClient(create_app(settings)) as client:
        client.get("/api/me")
        assert client.get("/api/setup").json() == {"setup": None, "run": None}
        assert client.post("/api/setup/apply", json={"draft_id": "x"}).status_code == 422

