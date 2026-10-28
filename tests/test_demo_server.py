"""The demo NAZAR answers like NAZAR: the recorded contracts, the agent's client, the cases."""

import io
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from PIL import Image

from nazar_agent.demo.contracts import missing, shape
from nazar_agent.demo.photos import FACES
from nazar_agent.demo.server import DEMO_AGENT_KEY, DEMO_EMAIL, DEMO_PASSWORD, DemoNazar, create_app
from nazar_agent.demo.timesheet import _absence_weekday
from nazar_agent.demo.world import TZ, build_world
from nazar_agent.nazar import NazarApiError, NazarClient

CONTRACTS = Path(__file__).parent / "contracts"
RECORDED = {path.stem: json.loads(path.read_text(encoding="utf-8")) for path in sorted(CONTRACTS.glob("*.json"))}
# A Wednesday, mid-afternoon: the same moment as the world's tests.
NOW = datetime(2026, 10, 28, 15, 30, tzinfo=TZ)
BASE = "http://demo-nazar"


def demo() -> DemoNazar:
    return DemoNazar(build_world(NOW), clock=lambda: NOW.astimezone(UTC))


def http(state: DemoNazar, **headers: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(state)), base_url=BASE, headers=headers)


@asynccontextmanager
async def signed_in(state: DemoNazar) -> AsyncIterator[httpx.AsyncClient]:
    async with http(state) as client:
        response = await client.post(
            "/ai-agent/service-session", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD},
            headers={"X-Agent-Key": DEMO_AGENT_KEY},
        )
        client.headers["Authorization"] = f"Bearer {response.json()['data']['access_token']}"
        yield client


def face_jpeg(size: int = 480) -> bytes:
    buffer = io.BytesIO()
    Image.open(FACES / "azamat_normatov.jpg").convert("RGB").resize((size, size)).save(buffer, "JPEG")
    return buffer.getvalue()


def people(state: DemoNazar, name: str) -> str:
    return next(person.id for person in state.world.people.values() if person.name == name)


async def answers() -> dict[str, httpx.Response]:
    """Every recorded request, made to the demo NAZAR in the recorder's order."""
    state = demo()
    got: dict[str, httpx.Response] = {}
    async with http(state) as cookies:
        got["auth_login"] = await cookies.post("/auth/login", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})
        got["auth_refresh"] = await cookies.post("/auth/refresh")
    async with http(state) as client:
        key = {"X-Agent-Key": DEMO_AGENT_KEY}
        got["ai_agent_service_session"] = await client.post(
            "/ai-agent/service-session", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD}, headers=key
        )
        client.headers["Authorization"] = f"Bearer {got['ai_agent_service_session'].json()['data']['access_token']}"
        got["ai_agent_handoff"] = await client.post("/ai-agent/handoff")
        handoff = got["ai_agent_handoff"].json()["data"]["url"].split("#handoff=", 1)[1]
        got["ai_agent_session"] = await client.post("/ai-agent/session", json={"handoff": handoff}, headers=key)
        got["ai_agent_usage"] = await client.post("/ai-agent/usage", headers=key, json={
            "company_id": state.world.company_id, "run_id": "contract", "source": "ask",
            "calls": [{"role": "dispatcher", "model": "muse-spark-1.3", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0}],
        })

        today = NOW.date()
        got["objects"] = await client.get("/objects", params={"include_inactive": "false"})
        site = got["objects"].json()["data"][0]
        got["object_assignments"] = await client.get(f"/objects/{site['id']}/assignments")
        got["employees"] = await client.get("/employees")
        got["attendance_logs"] = await client.get("/attendance/logs", params={
            "date_from": (NOW - timedelta(days=14)).isoformat(), "date_to": (NOW + timedelta(days=1)).isoformat(), "limit": 50,
        })
        event = got["attendance_logs"].json()["data"][0]
        got["attendance_event"] = await client.get(f"/attendance/events/{event['id']}")
        got["attendance_presence"] = await client.get("/attendance/presence", params={"status": "all", "limit": 100})
        got["timesheet"] = await client.get(
            "/timesheet", params={"date_from": (today - timedelta(days=7)).isoformat(), "date_to": today.isoformat()}
        )
        worked = next(row for row in got["timesheet"].json()["data"]["days"] if row["day_code"] == "work")
        got["timesheet_day_detail"] = await client.get(
            "/timesheet/day/detail", params={"employee_id": worked["employee_id"], "work_date": worked["work_date"]}
        )
        got["timesheet_setup_check"] = await client.get("/timesheet/setup-check")
        got["media_photo"] = await client.get(event["photo_url"])
        got["attendance_review"] = await client.post(
            f"/attendance/events/{event['id']}/review", json={"status": "insufficient_data", "comment": "contract"}
        )

        item = {
            "employee_id": worked["employee_id"], "work_date": worked["work_date"],
            "assignment_id": worked["shift_breakdown"][0]["assignment_id"],
        }
        start = datetime.fromisoformat(f"{worked['work_date']}T09:00:00+05:00")
        intervals = [{"start_at": start.isoformat(), "end_at": (start + timedelta(hours=8)).isoformat()}]
        got["timesheet_adjustments_preview"] = await client.post(
            "/timesheet/adjustments/preview", json={"items": [{**item, "intervals": intervals}]}
        )
        got["timesheet_adjustment"] = await client.post(
            "/timesheet/adjustments", json={**item, "intervals": intervals, "reason": "contract"}
        )
        got["timesheet_adjustment_delete"] = await client.delete(
            f"/timesheet/adjustments/{got['timesheet_adjustment'].json()['data']['id']}"
        )
        got["timesheet_void"] = await client.post("/timesheet/adjustments/void", json={**item, "reason": "contract"})
        got["timesheet_void_delete"] = await client.delete(
            f"/timesheet/adjustments/void/{got['timesheet_void'].json()['data']['id']}"
        )
        got["object_update"] = await client.patch(f"/objects/{site['id']}", json={"shift_start": site["shift_start"]})
        assigned = [entry["employee_id"] for entry in got["object_assignments"].json()["data"]["assignments"]]
        got["object_assignments_replace"] = await client.post(
            f"/objects/{site['id']}/assignments", json={"employee_ids": assigned, "start_date": today.isoformat()}
        )
        picture = face_jpeg()
        got["face_quality_check"] = await client.post(
            "/face/quality-check", files=[("photo", ("photo.jpg", picture, "image/jpeg"))]
        )
        got["employee_create"] = await client.post(
            "/employees", data={"full_name": "Контракт Проверка"}, files=[("photos", ("photo.jpg", picture, "image/jpeg"))]
        )
        got["error_404"] = await client.get("/attendance/events/00000000-0000-4000-8000-000000000000")
        got["error_422"] = await client.get("/timesheet", params={"date_from": "not-a-date", "date_to": today.isoformat()})
    return got


@pytest.fixture(scope="module")
def demo_answers() -> dict[str, httpx.Response]:
    import asyncio

    return asyncio.run(answers())


def test_the_demo_answers_every_recorded_request(demo_answers):
    assert set(RECORDED) == set(demo_answers)


@pytest.mark.parametrize("name", sorted(RECORDED))
def test_the_demo_answers_with_nazars_fields_and_types(name, demo_answers):
    real, response = RECORDED[name], demo_answers[name]

    assert response.status_code == real["status"], response.text[:300]
    if name == "media_photo":
        assert response.headers["content-type"] == real["content_type"] and len(response.content) > 1000
        return
    assert missing(shape(_comparable(name, real["body"])), shape(_comparable(name, response.json()))) == []


def _comparable(name: str, body: dict) -> dict:
    """A setup finding's details depend on its code; they are checked per code below."""
    if name != "timesheet_setup_check":
        return body
    findings = [{key: value for key, value in item.items() if key != "details"} for item in body["data"]["findings"]]
    return {**body, "data": {**body["data"], "findings": findings}}


def test_a_weekday_finding_has_nazars_details():
    real = RECORDED["timesheet_setup_check"]["body"]["data"]["findings"][0]
    rows = [
        {"day_code": "absent", "work_date": day, "employee_id": "a"}
        for day in ("2026-10-06", "2026-10-13", "2026-10-20", "2026-10-27", "2026-10-14", "2026-10-21")
    ]

    ours = _absence_weekday(rows, {"a": "A"})

    assert real["code"] == ours["code"] == "absences_same_weekday"
    assert missing(shape(real), shape(ours)) == [] and ours["details"]["weekday"] == 2


async def test_the_agents_client_signs_in_with_the_key_and_reads():
    state = demo()
    client = NazarClient(
        base_url=BASE, email=DEMO_EMAIL, password=DEMO_PASSWORD, agent_key=DEMO_AGENT_KEY,
        transport=httpx.ASGITransport(app=create_app(state)),
    )
    sites = await client.get("/objects")

    assert {site["name"] for site in sites} == {site.name for site in state.world.sites.values()}
    assert client.company_id == state.world.company_id
    await client.aclose()


async def test_the_agents_client_falls_back_to_cookies_without_the_key():
    client = NazarClient(
        base_url=BASE, email=DEMO_EMAIL, password=DEMO_PASSWORD, transport=httpx.ASGITransport(app=create_app(demo()))
    )

    assert len(await client.get("/employees")) == 16
    await client.aclose()


async def test_wrong_credentials_and_no_session_are_refused_as_nazar_does():
    async with http(demo()) as client:
        login = await client.post("/auth/login", json={"email": DEMO_EMAIL, "password": "wrong"})
        anonymous = await client.get("/objects")
        no_key = await client.post("/ai-agent/service-session", json={"email": DEMO_EMAIL, "password": DEMO_PASSWORD})

    assert login.status_code == 401 and login.json()["error"]["code"] == "INVALID_CREDENTIALS"
    assert anonymous.status_code == 401 and anonymous.json()["success"] is False
    assert no_key.status_code == 403


async def test_a_handoff_pass_works_once():
    state = demo()
    async with signed_in(state) as client:
        url = (await client.post("/ai-agent/handoff")).json()["data"]["url"]
        handoff = {"handoff": url.split("#handoff=", 1)[1]}
        first = await client.post("/ai-agent/session", json=handoff, headers={"X-Agent-Key": DEMO_AGENT_KEY})
        second = await client.post("/ai-agent/session", json=handoff, headers={"X-Agent-Key": DEMO_AGENT_KEY})

    assert url.startswith("http://localhost:8100/#handoff=")
    assert first.status_code == 200 and second.status_code == 401


async def test_the_timesheet_counts_the_late_crew_as_late_every_day():
    state = demo()
    zuhra = people(state, "Зухра Набиева")
    async with signed_in(state) as client:
        rows = (await client.get("/timesheet", params={
            "date_from": "2026-10-19", "date_to": "2026-10-27", "employee_id": zuhra,
        })).json()["data"]["days"]
    worked = [row for row in rows if row["day_code"] == "work"]

    assert {row["day_code"] for row in rows} == {"work", "day_off"}  # Sundays off
    assert all(45 <= row["late_minutes"] <= 75 and row["overtime_minutes"] > 0 for row in worked)
    assert all(row["worked_minutes"] == row["paid_minutes"] for row in worked)


async def test_today_is_pending_and_a_shift_without_check_out_is_open():
    state = demo()
    async with signed_in(state) as client:
        rows = (await client.get("/timesheet", params={"date_from": "2026-10-28", "date_to": "2026-10-28"})).json()["data"]["days"]

    assert {row["day_code"] for row in rows} == {"pending"}
    assert sum(row["has_open_session"] for row in rows) >= 14
    assert all(row["shortfall_minutes"] == 0 for row in rows)


async def test_the_setup_check_finds_the_wrong_schedule_and_no_false_weekday():
    state = demo()
    async with signed_in(state) as client:
        check = (await client.get("/timesheet/setup-check")).json()["data"]
    findings = {finding["code"]: finding for finding in check["findings"]}

    assert set(findings) == {"mass_lateness"}
    assert [site["object_name"] for site in findings["mass_lateness"]["details"]["objects"]] == ["ЖК «Яшнабад», отделка"]
    assert set(findings["mass_lateness"]["employee_names"]) == {"Зухра Набиева", "Камола Ташпулатова", "Дилфуза Эргашева"}
    assert check["date_to"] == "2026-10-27"


async def test_a_preview_is_what_applying_gives_and_undo_puts_the_day_back():
    state = demo()
    azamat = people(state, "Азамат Норматов")
    day = {"employee_id": azamat, "work_date": "2026-10-26"}
    async with signed_in(state) as client:
        before = (await client.get("/timesheet/day/detail", params=day)).json()["data"]["day"]
        target = {**day, "assignment_id": before["shift_breakdown"][0]["assignment_id"]}
        intervals = [{"start_at": "2026-10-26T08:00:00+05:00", "end_at": "2026-10-26T18:00:00+05:00"}]
        preview = (await client.post("/timesheet/adjustments/preview", json={"items": [{**target, "intervals": intervals}]})).json()["data"][0]
        unchanged = (await client.get("/timesheet/day/detail", params=day)).json()["data"]["day"]
        saved = (await client.post("/timesheet/adjustments", json={**target, "intervals": intervals, "reason": "forgot to check out"})).json()["data"]
        applied = (await client.get("/timesheet/day/detail", params=day)).json()["data"]
        await client.delete(f"/timesheet/adjustments/{saved['id']}")
        undone = (await client.get("/timesheet/day/detail", params=day)).json()["data"]["day"]

    assert unchanged == before
    assert preview == applied["day"] and preview["worked_minutes"] == 600 and preview["is_adjusted"]
    assert [item["reason"] for item in applied["adjustments"]] == ["forgot to check out"]
    assert undone == before


async def test_a_voided_day_is_absent_and_comes_back():
    state = demo()
    timur = people(state, "Тимур Хасанов")
    day = {"employee_id": timur, "work_date": "2026-10-27"}
    async with signed_in(state) as client:
        before = (await client.get("/timesheet/day/detail", params=day)).json()["data"]["day"]
        void = (await client.post("/timesheet/adjustments/void", json={
            **day, "assignment_id": before["shift_breakdown"][0]["assignment_id"], "reason": "left 10 days ago",
        })).json()["data"]
        voided = (await client.get("/timesheet/day/detail", params=day)).json()["data"]["day"]
        wrong_route = await client.delete(f"/timesheet/adjustments/{void['id']}")
        await client.delete(f"/timesheet/adjustments/void/{void['id']}")
        back = (await client.get("/timesheet/day/detail", params=day)).json()["data"]["day"]

    assert before["day_code"] == "work" and before["paid_minutes"] > 500
    assert voided["day_code"] == "absent" and voided["paid_minutes"] == 0 and void["new_value"] == []
    assert wrong_route.status_code == 404
    assert back == before


async def test_an_adjustment_must_name_the_employees_own_shift():
    state = demo()
    azamat, zuhra = people(state, "Азамат Норматов"), people(state, "Зухра Набиева")
    async with signed_in(state) as client:
        foreign = await client.post("/timesheet/adjustments/void", json={
            "employee_id": azamat, "work_date": "2026-10-26", "object_id": state.world.people[zuhra].site_id, "reason": "x",
        })
        backwards = await client.post("/timesheet/adjustments/preview", json={"items": [{
            "employee_id": azamat, "work_date": "2026-10-26", "object_id": state.world.people[azamat].site_id,
            "intervals": [{"start_at": "2026-10-26T18:00:00+05:00", "end_at": "2026-10-26T08:00:00+05:00"}],
        }]})

    assert foreign.status_code == 422 and "not this employee's" in foreign.json()["error"]["message"]
    assert backwards.status_code == 422


async def test_a_schedule_change_applies_from_today_and_the_past_stays():
    state = demo()
    yashnobod = next(site for site in state.world.sites.values() if site.key == "yashnobod")
    zuhra = people(state, "Зухра Набиева")
    async with signed_in(state) as client:
        updated = (await client.patch(f"/objects/{yashnobod.id}", json={
            "shift_start": "09:00", "shift_end": "19:00", "work_schedule_mode": "custom",
        })).json()["data"]
        past = (await client.get("/timesheet/day/detail", params={"employee_id": zuhra, "work_date": "2026-10-27"})).json()["data"]["day"]
        today = (await client.get("/timesheet/day/detail", params={"employee_id": zuhra, "work_date": "2026-10-28"})).json()["data"]["day"]
        bad = await client.patch(f"/objects/{yashnobod.id}", json={"shift_start": "25:00"})

    assert (updated["shift_start"], updated["shift_end"], updated["work_schedule_mode"]) == ("09:00", "19:00", "custom")
    assert past["late_minutes"] > 45
    assert today["late_minutes"] == 0
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "VALIDATION_ERROR"


async def test_a_review_is_kept_and_can_be_put_back():
    state = demo()
    event = state.world.events[0]
    async with signed_in(state) as client:
        reviewed = (await client.post(f"/attendance/events/{event.id}/review", json={"status": "confirmed", "comment": " dead soul "})).json()["data"]
        back = (await client.post(f"/attendance/events/{event.id}/review", json={"status": "not_reviewed", "comment": None})).json()["data"]
        wrong = await client.post(f"/attendance/events/{event.id}/review", json={"status": "fined"})

    assert (reviewed["review_status"], reviewed["review_comment"], reviewed["reviewed_by_name"]) == ("confirmed", "dead soul", "Демо Стройка")
    assert back["review_status"] == "not_reviewed" and back["review_comment"] is None
    assert wrong.status_code == 422


async def test_presence_today_shows_who_is_on_site():
    state = demo()
    async with signed_in(state) as client:
        page = (await client.get("/attendance/presence", params={"status": "all", "limit": 100})).json()["data"]
        absent = (await client.get("/attendance/presence", params={"status": "absent", "limit": 100})).json()["data"]

    summary = page["summary"]
    assert summary["total_employees"] == 16 and summary["scheduled_today"] == 16
    assert summary["on_site"] == sum(1 for item in page["items"] if item["status"] == "in") >= 14
    assert summary["not_arrived"] == 16 - summary["on_site"]
    assert {item["status"] for item in absent["items"]} <= {"absent"} and absent["total"] == 16 - summary["on_site"]
    assert len(page["items"][0]["chart"]) == 30


async def test_logs_filter_and_page_newest_first():
    state = demo()
    sherzod = people(state, "Шерзод Алимов")
    async with signed_in(state) as client:
        flagged = (await client.get("/attendance/logs", params={"suspicious_only": "true", "limit": 500})).json()["data"]
        first = (await client.get("/attendance/logs", params={"limit": 5})).json()["data"]
        second = (await client.get("/attendance/logs", params={"limit": 5, "offset": 5})).json()["data"]

    assert flagged and {item["employee_id"] for item in flagged} == {sherzod}
    assert all("accuracy_too_low" in item["suspicious_flags"] for item in flagged)
    moments = [item["received_at_server"] for item in first + second]
    assert moments == sorted(moments, reverse=True) and len(set(moments)) == 10


async def test_a_new_employee_joins_a_site_from_today():
    state = demo()
    chilonzor = next(site for site in state.world.sites.values() if site.key == "chilonzor")
    client_ = NazarClient(
        base_url=BASE, email=DEMO_EMAIL, password=DEMO_PASSWORD, agent_key=DEMO_AGENT_KEY,
        transport=httpx.ASGITransport(app=create_app(state)),
    )
    picture = face_jpeg()
    created = await client_.post_form("/employees", {"full_name": "Нилуфар Рахимова"}, [("photos", ("p.jpg", picture, "image/jpeg"))])
    current = await client_.get(f"/objects/{chilonzor.id}/assignments")
    ids = [item["employee_id"] for item in current["assignments"]] + [created["id"]]
    after = await client_.post(f"/objects/{chilonzor.id}/assignments", {"employee_ids": ids, "start_date": "2026-10-28"})
    rows = (await client_.get("/timesheet", {"date_from": "2026-10-27", "date_to": "2026-10-28", "employee_id": created["id"]}))["days"]
    photo_back = await client_.get_bytes(created["preview_image_url"])

    assert created["employee_id"] == "MQ-017"
    assert created["id"] in {item["employee_id"] for item in after["assignments"]}
    assert [row["day_code"] for row in rows] == ["day_off", "pending"]
    assert photo_back == picture
    await client_.aclose()


async def test_a_picture_that_is_not_a_photo_is_refused_as_nazar_refuses():
    state = demo()
    client_ = NazarClient(
        base_url=BASE, email=DEMO_EMAIL, password=DEMO_PASSWORD, agent_key=DEMO_AGENT_KEY,
        transport=httpx.ASGITransport(app=create_app(state)),
    )
    small = await client_.post_form("/face/quality-check", {}, [("photo", ("p.jpg", face_jpeg(120), "image/jpeg"))])
    with pytest.raises(NazarApiError) as refused:
        await client_.post_form("/face/quality-check", {}, [("photo", ("p.jpg", b"not a picture", "image/jpeg"))])

    assert small["face_detected"] is False and small["blocking_reasons"]
    assert refused.value.status_code == 422
    await client_.aclose()


async def test_reset_brings_the_company_back():
    state = demo()
    event = state.world.events[0]
    async with signed_in(state) as client:
        await client.post(f"/attendance/events/{event.id}/review", json={"status": "confirmed"})
    state.reset(build_world(NOW))

    assert next(item for item in state.world.events if item.id == event.id).review_status == "not_reviewed"
    assert state.book.adjustments == []
