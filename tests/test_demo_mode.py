"""DEMO_MODE: the agent on the demo company in this process, with only a model key."""

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from nazar_agent.config import Settings
from nazar_agent.demo.server import DemoNazar
from nazar_agent.demo.server import create_app as demo_nazar
from nazar_agent.demo.world import TZ
from nazar_agent.nazar.connect import DEMO_BASE_URL, connect
from nazar_agent.service import create_app
from tests import factories


def settings(tmp_path, **values) -> Settings:
    return Settings(_env_file=None, model_api_key="k", agent_db_path=str(tmp_path / "agent.sqlite3"), **values)


def test_the_demo_needs_no_nazar_and_the_real_setup_does(tmp_path):
    assert settings(tmp_path, demo_mode=True).has_service_account
    with pytest.raises(ValidationError, match="DEMO_MODE"):
        settings(tmp_path)


def test_the_demo_ignores_a_real_nazar_and_signs_in_on_its_own(tmp_path):
    link = connect(settings(tmp_path, demo_mode=True, nazar_base_url="https://nazarhr.uz/api", nazar_email="real@x"))

    assert link.base_url == DEMO_BASE_URL and link.demo is not None
    assert link.email != "real@x" and link.agent_key


def test_without_its_own_key_the_skeptic_runs_on_meta(tmp_path):
    baseten = {
        "demo_mode": True, "skeptic_model": "meta-models/muse-spark-open-1026", "skeptic_api": "chat_completions",
        "skeptic_base_url": "https://inference.baseten.co/v1",
    }

    fallback = settings(tmp_path, **baseten)
    own = settings(tmp_path, **baseten, skeptic_api_key="baseten-key")

    assert (fallback.skeptic_model, fallback.skeptic_api, fallback.skeptic_base_url) == ("muse-spark-1.2", "responses", None)
    assert (own.skeptic_model, own.skeptic_api) == ("meta-models/muse-spark-open-1026", "chat_completions")


@pytest.fixture
def demo_window(tmp_path):
    app = create_app(settings(tmp_path, demo_mode=True, require_sign_in=True))
    with TestClient(app) as client:
        yield client, app.state.link.demo


def test_the_demo_window_opens_at_once_as_the_demo_owner(demo_window):
    client, _ = demo_window

    me = client.get("/api/me")

    assert me.status_code == 200 and me.json()["mode"] == "demo"
    assert me.json()["company_name"] == "Mustahkam Qurilish"
    assert len(client.cookies.get("nazar_agent_demo") or "") >= 20  # each browser counted on its own


def test_the_demo_window_works_on_the_demo_company(demo_window):
    client, demo = demo_window
    chilonzor = next(site for site in demo.world.sites.values() if site.key == "chilonzor")

    drafts = client.post("/api/import/drafts", json={"rows": [{"full_name": "Нилуфар Рахимова", "site_id": chilonzor.id}]})

    assert drafts.status_code == 200 and drafts.json()[0]["site_name"] == chilonzor.name


def test_reset_brings_the_demo_company_back(demo_window):
    client, demo = demo_window
    event = demo.world.events[0]
    event.review_status = "confirmed"

    response = client.post("/api/demo/reset", json={})

    assert response.status_code == 200
    assert next(item for item in demo.world.events if item.id == event.id).review_status == "not_reviewed"


def test_there_is_no_reset_outside_the_demo(tmp_path):
    real = settings(tmp_path, nazar_base_url="http://nazar.test/api", nazar_email="a", nazar_password="b")
    route, _ = factories.nazar_routes()
    with TestClient(create_app(real, nazar_transport=httpx.MockTransport(route))) as client:
        assert client.post("/api/demo/reset", json={}).status_code == 404
        assert client.get("/api/me").json()["mode"] == "service"


async def test_the_demo_day_fills_in_with_the_clock_and_a_new_day_starts_over():
    moment = {"now": datetime(2026, 10, 28, 7, 0, tzinfo=TZ)}
    demo = DemoNazar.today(lambda: moment["now"].astimezone(UTC))
    signed = {"email": "owner@demo.nazar", "password": "demo"}

    async def check_ins_today() -> tuple[int, int]:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=demo_nazar(demo)), base_url="http://d") as http:
            await http.post("/auth/login", json=signed)
            day = moment["now"].date().isoformat()
            rows = (await http.get("/timesheet", params={"date_from": day, "date_to": day})).json()["data"]["days"]
        return sum(row["has_open_session"] for row in rows), sum(row["day_code"] == "work" for row in rows)

    before_work = await check_ins_today()
    moment["now"] = moment["now"].replace(hour=12)
    midday = await check_ins_today()
    moment["now"] = moment["now"].replace(hour=20)
    evening = await check_ins_today()
    first_event = demo.world.events[0]
    first_event.review_status = "confirmed"
    moment["now"] += timedelta(days=1)
    await check_ins_today()

    assert before_work == (0, 0)
    assert midday[0] >= 14 and midday[1] == 0  # on site, the day not final yet
    assert evening[0] == 0 and evening[1] >= 14  # everyone checked out
    assert demo.world.today == datetime(2026, 10, 29).date()
    assert all(event.review_status == "not_reviewed" for event in demo.world.events)


def test_every_demo_browser_asks_on_its_own_over_one_company(demo_window):
    client, _ = demo_window
    other = TestClient(client.app)

    client.get("/api/me")
    other.get("/api/me")
    assistants = client.app.state.assistants
    first = assistants.for_visitor(client.cookies.get("nazar_agent_demo"))
    second = assistants.for_visitor(other.cookies.get("nazar_agent_demo"))

    assert first is not second
    assert first.store is second.store  # one company, one audit trail


def test_the_one_command_demo_says_what_is_missing(monkeypatch, capsys):
    from nazar_agent.demo.__main__ import main

    monkeypatch.delenv("MODEL_API_KEY", raising=False)
    monkeypatch.chdir(Path(__file__).parent)  # no .env here
    monkeypatch.setattr("sys.argv", ["nazar_agent.demo"])

    assert main() == 2
    assert "MODEL_API_KEY" in capsys.readouterr().err
