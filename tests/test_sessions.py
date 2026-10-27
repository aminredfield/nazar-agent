"""The window opened from the NAZAR cabinet works as that person, with their rights."""

import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from nazar_agent.actions import ProposalStore
from nazar_agent.config import Settings
from nazar_agent.service import create_app
from tests import factories
from tests.test_executor import propose

AGENT_KEY = "k" * 40
COMPANY = str(uuid.uuid4())
PASS = "pass-" + "x" * 30


def nazar_with_sessions():
    """The fake NAZAR, plus the pass exchange that checks the assistant key."""
    route, seen = factories.nazar_routes()

    def with_session(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ai-agent/session"):
            seen.append(request)
            body = json.loads(request.content)
            if request.headers.get("x-agent-key") != AGENT_KEY or body["handoff"] != PASS:
                return httpx.Response(401, json={"success": False, "error": {"code": "UNAUTHORIZED", "message": "no"}})
            return factories.ok({
                "access_token": "person-token",
                "expires_at": (datetime.now(UTC) + timedelta(minutes=30)).isoformat(),
                "company_id": COMPANY,
                "company_name": "Стройка Чиланзар",
                "full_name": "Шерзод Каримов",
                "email": "sherzod@example.com",
                "role": "owner",
            })
        return route(request)

    return with_session, seen


def client_for(tmp_path, **overrides):
    values = {
        "_env_file": None,
        "model_api_key": "k",
        "nazar_base_url": "http://nazar.test/api",
        "nazar_agent_key": AGENT_KEY,
        "agent_db_path": str(tmp_path / "agent.sqlite3"),
        **overrides,
    }
    route, seen = nazar_with_sessions()
    app = create_app(Settings(**values), nazar_transport=httpx.MockTransport(route))
    return TestClient(app), seen


def test_pass_opens_a_session_and_the_window_acts_as_that_person(tmp_path):
    with client_for(tmp_path)[0] as client:
        opened = client.post("/api/session", json={"handoff": PASS})
        assert opened.status_code == 200 and opened.json()["full_name"] == "Шерзод Каримов"
        assert "nazar_agent_session" in opened.headers["set-cookie"].lower()
        assert "httponly" in opened.headers["set-cookie"].lower()
        assert client.get("/api/me").json()["company_name"] == "Стройка Чиланзар"


def test_decisions_go_to_nazar_with_the_persons_token_and_name(tmp_path):
    client, seen = client_for(tmp_path)
    with client:
        client.post("/api/session", json={"handoff": PASS})
        company_store = ProposalStore(tmp_path / f"agent-{COMPANY}.sqlite3")
        proposal = propose(company_store)

        applied = client.post(f"/api/proposals/{proposal.id}/confirm", json={"actor": "Кто-то другой"})

    assert applied.status_code == 200, applied.text
    write = next(r for r in seen if r.method == "POST" and r.url.path.endswith("/timesheet/adjustments"))
    assert write.headers["authorization"] == "Bearer person-token"
    assert not any(r.url.path.endswith("/auth/login") for r in seen)  # never the service account
    assert company_store.audit(proposal_id=proposal.id)[0].actor == "Шерзод Каримов (NAZAR)"


def test_each_company_has_its_own_proposals(tmp_path):
    client, _ = client_for(tmp_path, nazar_email="service@example.com", nazar_password="secret")
    with client:
        client.post("/api/session", json={"handoff": PASS})
        service_proposal = propose(ProposalStore(tmp_path / "agent.sqlite3"))

        foreign = client.post(f"/api/proposals/{service_proposal.id}/confirm", json={})

    assert foreign.status_code == 404  # another company's proposal does not exist for this person


def test_a_wrong_or_stale_pass_opens_nothing(tmp_path):
    with client_for(tmp_path)[0] as client:
        refused = client.post("/api/session", json={"handoff": "pass-" + "y" * 30})

        assert refused.status_code == 401 and "кабинета NAZAR" in refused.json()["detail"]
        assert client.get("/api/me").status_code == 401


def test_production_window_needs_the_cabinet(tmp_path):
    client, _ = client_for(tmp_path, nazar_email="service@example.com", nazar_password="secret", require_sign_in=True)
    with client:
        assert client.get("/api/audit").status_code == 401


def test_locally_the_service_account_still_works(tmp_path):
    client, _ = client_for(tmp_path, nazar_email="service@example.com", nazar_password="secret")
    with client:
        assert client.get("/api/me").json() == {"mode": "service"}


@pytest.mark.parametrize("company", ["../../etc/passwd", "not-a-uuid"])
def test_company_ids_cannot_reach_outside_the_data_folder(tmp_path, company):
    from nazar_agent.assistant import Assistants

    assistants = Assistants(Settings(_env_file=None, model_api_key="k", nazar_base_url="x", agent_db_path=str(tmp_path / "a.sqlite3")))

    with pytest.raises(ValueError):
        assistants.for_company(company)
