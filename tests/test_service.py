from types import SimpleNamespace

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
    lock = client.app.state.assistants.service._lock  # lives on the app's event loop
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
    client.app.state.assistants.service.last = SimpleNamespace(
        view={"summary": "s", "cards": [{"title": "t", "actions": [{"proposal_id": proposal.id}]}]}
    )
    client.post(f"/api/proposals/{proposal.id}/confirm", json={"actor": "Шерзод"})

    [card] = client.get("/api/last").json()["cards"]
    assert card["actions"][0]["status"] == "executed"


def test_import_from_rows_to_an_employee_in_nazar(setup):
    client, store, seen = setup

    created = client.post("/api/import/drafts", json={
        "actor": "Шерзод",
        "rows": [{"full_name": "  Иванов   Алишер ", "site_id": factories.SITE}, {"full_name": "Сидоров", "site_id": None}],
    })
    [ivanov, sidorov] = created.json()
    photo = client.post(f"/api/import/drafts/{ivanov['id']}/photo", data={"actor": "Шерзод"},
                        files={"photo": ("face.jpg", b"jpeg", "image/jpeg")})
    removed = client.post(f"/api/import/drafts/{sidorov['id']}/discard", json={"actor": "Шерзод"})

    assert ivanov["full_name"] == "Иванов Алишер" and ivanov["site_name"] == "Магазин Чиланзар"
    assert photo.status_code == 200 and photo.json()["status"] == "created"
    assert removed.json()["status"] == "discarded"
    assert client.get("/api/import/drafts").json() == []
    events = [entry["event"] for entry in client.get("/api/audit").json()]
    assert events[:2] == ["draft_discarded", "employee_created"]


def test_import_refuses_an_unknown_site_and_a_bad_photo(setup, monkeypatch):
    client, _, _ = setup
    unknown = client.post("/api/import/drafts", json={"actor": "x", "rows": [{"full_name": "Иванов Алишер", "site_id": "nope"}]})
    [draft] = client.post("/api/import/drafts", json={"actor": "x", "rows": [{"full_name": "Иванов Алишер"}]}).json()
    monkeypatch.setattr(factories, "PHOTO_CHECK", {"face_detected": False, "blocking_reasons": ["FACE_NOT_FOUND: Лицо не найдено."]})

    bad = client.post(f"/api/import/drafts/{draft['id']}/photo", data={"actor": "x"}, files={"photo": ("f.jpg", b"x", "image/jpeg")})

    assert unknown.status_code == 422
    assert bad.status_code == 422 and bad.json()["detail"] == {"photo_rejected": ["Лицо не найдено."]}
