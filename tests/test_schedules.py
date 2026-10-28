import json

import httpx
import pytest

from nazar_agent.engine import ToolExecutionError, ToolStep
from nazar_agent.findings import Evidence
from nazar_agent.nazar import NazarClient
from nazar_agent.recommendations import Context, SkepticView, validate_action
from nazar_agent.recommendations.catalog import CATALOG, ChangeScheduleParams
from nazar_agent.tools.schedules import site_schedules
from tests.factories import ok

SITE = "aaaaaaaa-0000-0000-0000-000000000001"
OTHER = "aaaaaaaa-0000-0000-0000-000000000002"
OBJECT = {
    "id": SITE,
    "name": "ЖК «Яшнабад», отделка",
    "address": "ул. Махтумкули, 95",
    "latitude": 41.3,
    "longitude": 69.3,
    "radius_meters": 100.0,
    "is_active": True,
    "shift_start": "08:00",
    "shift_end": "18:00",
    "max_shift_hours": 12,
    "late_grace_minutes": None,
    "early_leave_grace_minutes": 0,
    "overtime_after_minutes": 0,
    "max_overtime_minutes": 240,
    "auto_close_after_minutes": 360,
    "work_schedule_mode": "company_default",
    "working_days": [1, 2, 3, 4, 5, 6],
    "trusted_networks": [],
    "assigned_employee_count": 3,
}
MEDIUM = Context("medium", SkepticView("concern_supported", "r", (), ()))


def event(kind, employee, at, site=SITE):
    return {"event_type": kind, "employee_id": employee, "object_id": site, "received_at_server": at}


def test_site_schedule_shows_the_real_day_next_to_the_set_one():
    events = [
        event("check_in", "e1", "2026-10-26T04:01:00+00:00"),  # 09:01 in Tashkent
        event("check_in", "e1", "2026-10-26T04:30:00+00:00"),  # a second check-in that day does not count
        event("check_out", "e1", "2026-10-26T14:02:00+00:00"),  # 19:02
        event("check_in", "e2", "2026-10-26T03:58:00+00:00"),  # 08:58
        event("check_out", "e2", "2026-10-26T14:00:00+00:00"),
        event("check_in", "e1", "2026-10-27T04:00:00+00:00"),
        event("check_in", "e9", "2026-10-27T02:55:00+00:00", site=OTHER),
    ]
    rows = [
        {"object_id": SITE, "late_minutes": 61},
        {"object_id": SITE, "late_minutes": 0},
        {"object_id": OTHER, "late_minutes": 5},
    ]

    [site] = site_schedules([OBJECT], events, rows)

    assert site["shift"] == ["08:00", "18:00"]
    assert site["people"] == 2
    assert site["days_with_check_in"] == 3
    assert site["typical_first_check_in"] == "09:00"
    assert site["typical_last_check_out"] == "19:01"
    assert site["days_arrived_after_shift_start"] == 3
    assert site["days_left_after_shift_end"] == 2
    assert (site["nazar_late_days"], site["nazar_late_minutes"]) == (1, 61)


def fake_nazar():
    site = dict(OBJECT)
    seen: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path.endswith("/auth/login"):
            return ok({})
        if request.url.path.endswith("/objects") and request.method == "GET":
            return ok([{"id": OTHER, "name": "Другой"}, dict(site)])
        if request.url.path.endswith(f"/objects/{SITE}") and request.method == "PATCH":
            site.update(json.loads(request.content))
            return ok(dict(site))
        return httpx.Response(404, json={"success": False, "error": {"code": "NOT_FOUND", "message": request.url.path}})

    client = NazarClient(base_url="http://nazar.test/api", email="a", password="b", transport=httpx.MockTransport(route))
    return client, site, seen


def evidence_with_site() -> Evidence:
    step = ToolStep(call_id="c", name="compare_site_schedules", arguments={}, output={"sites": [{"id": SITE}]}, error=None, duration_ms=1)
    return Evidence.from_steps((step,))


PARAMS = {"object_id": SITE, "shift_start": "09:00", "shift_end": "19:00", "reason": "Бригада работает с 9 до 19"}


async def test_schedule_change_shows_before_and_after_from_nazar():
    nazar, _, _ = fake_nazar()

    action = await validate_action("change_schedule", PARAMS, context=MEDIUM, evidence=evidence_with_site(), nazar=nazar)

    assert action.valid and action.level == "changes_pay"
    assert action.preview.before == {
        "shift_start": "08:00", "shift_end": "18:00", "working_days": [1, 2, 3, 4, 5, 6], "work_schedule_mode": "company_default",
    }
    assert action.preview.after == {
        "shift_start": "09:00", "shift_end": "19:00", "working_days": [1, 2, 3, 4, 5, 6], "work_schedule_mode": "custom",
    }


async def test_schedule_change_needs_a_seen_site_and_some_confidence():
    nazar, _, _ = fake_nazar()
    low = Context("low", None)
    doubted = Context("medium", SkepticView("innocent_likely", "r", (), ()))

    unseen = await validate_action("change_schedule", PARAMS, context=MEDIUM, evidence=Evidence.from_steps(()), nazar=nazar)
    weak = await validate_action("change_schedule", PARAMS, context=low, evidence=evidence_with_site(), nazar=nazar)
    disputed = await validate_action("change_schedule", PARAMS, context=doubted, evidence=evidence_with_site(), nazar=nazar)
    bad_clock = await validate_action(
        "change_schedule", {**PARAMS, "shift_start": "9:00"}, context=MEDIUM, evidence=evidence_with_site(), nazar=nazar
    )

    assert "acts on records no tool returned" in unseen.problems[0]
    assert weak.problems == ("changing a site's schedule needs at least medium confidence",)
    assert disputed.problems == ("the skeptic finds another explanation likely",)
    assert bad_clock.problems and bad_clock.problems[0].startswith("shift_start")


async def test_schedule_change_keeps_the_rest_of_the_site_and_undoes_cleanly():
    nazar, site, seen = fake_nazar()
    spec = CATALOG["change_schedule"]
    params = ChangeScheduleParams.model_validate(PARAMS)

    result = await spec.execute(nazar, params)
    sent = json.loads(next(request for request in seen if request.method == "PATCH").content)
    await spec.undo(nazar, params, result)

    assert (sent["shift_start"], sent["shift_end"], sent["work_schedule_mode"]) == ("09:00", "19:00", "custom")
    assert (sent["name"], sent["radius_meters"], sent["latitude"]) == (OBJECT["name"], 100.0, 41.3)
    assert "id" not in sent and "assigned_employee_count" not in sent
    assert sum(request.method == "PATCH" and request.url.path.endswith(f"/objects/{SITE}") for request in seen) == 2
    assert (site["shift_start"], site["shift_end"], site["work_schedule_mode"]) == ("08:00", "18:00", "company_default")


async def test_undo_refuses_when_someone_changed_the_schedule_since():
    nazar, site, _ = fake_nazar()
    spec = CATALOG["change_schedule"]
    params = ChangeScheduleParams.model_validate(PARAMS)
    result = await spec.execute(nazar, params)
    site.update({"shift_start": "09:30"})  # an admin edited it in the cabinet

    with pytest.raises(ToolExecutionError, match="changed after this action"):
        await spec.undo(nazar, params, result)

    assert site["shift_start"] == "09:30"
