import httpx
import pytest
from fastapi.testclient import TestClient

from nazar_agent.actions import ProposalStore
from nazar_agent.config import Settings
from nazar_agent.service import create_app
from tests import factories
from tests.test_executor import propose


@pytest.fixture
def setup(tmp_path):
    settings = Settings(
        _env_file=None,
        model_api_key="k",
        nazar_base_url="http://nazar.test/api",
        nazar_email="a",
        nazar_password="b",
        agent_db_path=str(tmp_path / "agent.sqlite3"),
    )
    route, seen = factories.nazar_routes()
    app = create_app(settings, nazar_transport=httpx.MockTransport(route))
    store = ProposalStore(settings.agent_db_path)
    with TestClient(app) as client:
        yield client, store, seen


def test_window_is_served(setup):
    client, _, _ = setup

    response = client.get("/")

    assert response.status_code == 200 and "NAZAR · Помощник" in response.text


def test_apply_then_undo_through_the_api(setup):
    client, store, _ = setup
    proposal = propose(store)

    applied = client.post(f"/api/proposals/{proposal.id}/confirm", json={"actor": "Шерзод"})
    again = client.post(f"/api/proposals/{proposal.id}/confirm", json={"actor": "Шерзод"})
    undone = client.post(f"/api/proposals/{proposal.id}/undo", json={"actor": "Шерзод"})

    assert applied.status_code == 200 and applied.json()["status"] == "executed"
    assert again.status_code == 409
    assert undone.json()["status"] == "undone"
    audit = client.get("/api/audit").json()
    assert [entry["event"] for entry in audit] == ["undone", "executed", "proposed"]
    assert audit[0]["subject"] == "Зарина" and audit[0]["actor"] == "Шерзод"


def test_disagreeing_without_a_reason_is_rejected(setup):
    client, store, _ = setup
    proposal = propose(store)

    missing = client.post(f"/api/proposals/{proposal.id}/decline", json={"actor": "Шерзод", "reason": ""})
    declined = client.post(f"/api/proposals/{proposal.id}/decline", json={"actor": "Шерзод", "reason": "Нет телефонов"})

    assert missing.status_code == 422
    assert declined.json()["status"] == "declined"


def test_unknown_proposal_is_404(setup):
    client, _, _ = setup

    assert client.post("/api/proposals/nope/confirm", json={"actor": "x"}).status_code == 404


def test_one_question_at_a_time(setup):
    client, _, _ = setup
    lock = client.app.state.runtime.asking  # lives on the app's event loop
    client.portal.call(lock.acquire)
    try:
        busy = client.post("/api/ask", json={"question": "Что проверить?", "analyst": "timesheet"})
    finally:
        client.portal.call(lock.release)

    assert busy.status_code == 429


def test_last_answer_shows_proposals_as_they_are_now(setup):
    client, store, _ = setup
    assert client.get("/api/last").json() is None

    proposal = propose(store)
    client.app.state.runtime.last_answer = {"summary": "s", "cards": [{"title": "t", "actions": [{"proposal_id": proposal.id}]}]}
    client.post(f"/api/proposals/{proposal.id}/confirm", json={"actor": "Шерзод"})

    [card] = client.get("/api/last").json()["cards"]
    assert card["actions"][0]["status"] == "executed"
