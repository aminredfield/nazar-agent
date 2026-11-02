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
    "name": "Maple Court residential, finishing",
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


def evidence_with_schedule() -> Evidence:
    step = ToolStep(
        call_id="c", name="compare_site_schedules", arguments={},
        output={"sites": [{"id": SITE, "shift": ["08:00", "18:00"]}]}, error=None, duration_ms=1,
    )
    return Evidence.from_steps((step,))


async def test_a_schedule_with_one_end_missing_keeps_the_days_length():
    # The writer sometimes sends only the new start. The day stays as long as it is
    # now (08:00–18:00 in the evidence), so 09:00 becomes 09:00–19:00 — not a guess.
    nazar, _, _ = fake_nazar()
    no_end = {"object_id": SITE, "shift_start": "09:00", "reason": PARAMS["reason"]}
    no_start = {"object_id": SITE, "shift_end": "19:00", "reason": PARAMS["reason"]}

    filled_end = await validate_action("change_schedule", no_end, context=MEDIUM, evidence=evidence_with_schedule(), nazar=nazar)
    filled_start = await validate_action("change_schedule", no_start, context=MEDIUM, evidence=evidence_with_schedule(), nazar=nazar)

    assert filled_end.valid and (filled_end.params.shift_start, filled_end.params.shift_end) == ("09:00", "19:00")
    assert filled_start.valid and (filled_start.params.shift_start, filled_start.params.shift_end) == ("09:00", "19:00")


async def test_a_schedule_the_evidence_cannot_complete_still_goes_back_to_the_writer():
    nazar, _, _ = fake_nazar()
    no_end = {"object_id": SITE, "shift_start": "09:00", "reason": PARAMS["reason"]}
    nothing = {"object_id": SITE, "reason": PARAMS["reason"]}

    no_schedule_seen = await validate_action("change_schedule", no_end, context=MEDIUM, evidence=evidence_with_site(), nazar=nazar)
    both_missing = await validate_action("change_schedule", nothing, context=MEDIUM, evidence=evidence_with_schedule(), nazar=nazar)

    assert no_schedule_seen.fixable and no_schedule_seen.problems[0].startswith("shift_end")
    assert both_missing.fixable and {problem.split(":")[0] for problem in both_missing.problems} == {"shift_start", "shift_end"}


async def test_a_shorter_working_week_is_never_the_agents_to_propose_a_shifted_one_is_fine():
    # 08:00–18:00 → 09:00–17:00 is two hours a day less for the whole crew: the owner's own
    # decision, refused whatever the case's strength — and sent back so the writer can keep the day.
    strong = Context("high", SkepticView("concern_supported", "r", (), ()))
    shorter = {**PARAMS, "shift_start": "09:00", "shift_end": "17:00"}
    fewer_days = {**PARAMS, "shift_start": "08:00", "shift_end": "18:00", "working_days": [1, 2, 3, 4, 5]}
    night = {**PARAMS, "shift_start": "22:00", "shift_end": "08:00"}  # over midnight: ten hours, as before

    async def checked(params, context=MEDIUM):
        nazar, _, _ = fake_nazar()
        return await validate_action("change_schedule", params, context=context, evidence=evidence_with_site(), nazar=nazar)

    cut, days_cut, moved, at_night, still_cut = (
        await checked(shorter), await checked(fewer_days), await checked(PARAMS), await checked(night), await checked(shorter, strong)
    )

    assert "shorter working week" in cut.problems[0] and "60 → 48 h" in cut.problems[0] and cut.fixable
    assert "shorter working week" in days_cut.problems[0]
    assert moved.valid and at_night.valid
    assert not still_cut.valid and still_cut.fixable


async def test_the_live_today_card_shaving_an_hour_is_refused_even_with_a_strong_case():
    # Seen live on Nov 2: asked who is late today, the writer proposed 09:00–18:00 for a crew
    # that works 09:00–19:00 on a site set to 08:00–18:00 — one paid hour a day less for everyone,
    # and it passed because confidence was high and the skeptic agreed.
    strong = Context("high", SkepticView("concern_supported", "r", (), ()))
    nazar, _, _ = fake_nazar()
    shaved = {**PARAMS, "shift_start": "09:00", "shift_end": "18:00"}

    action = await validate_action("change_schedule", shaved, context=strong, evidence=evidence_with_site(), nazar=nazar)

    assert not action.valid and action.fixable
    assert "60 → 54 h" in action.problems[0] and "owner's own decision" in action.problems[0]


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
