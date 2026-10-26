import json

from nazar_agent.recommendations import Context, SkepticView, validate_action
from tests import factories

STRONG = Context("high", SkepticView("concern_supported", "r", (), ()))
WEAK = Context("medium", SkepticView("inconclusive", "r", (), ()))


def shift(start="2026-10-23T09:06:00+05:00", end="2026-10-23T17:49:00+05:00", **overrides):
    return {
        "employee_id": factories.EMPLOYEE,
        "work_date": factories.DAY,
        "intervals": [{"start_at": start, "end_at": end}],
        "reason": "Count from the real arrival",
        **overrides,
    }


async def validate(name, params, context=WEAK, preview_status=200):
    nazar, seen = factories.nazar(preview_status)
    action = await validate_action(name, params, context=context, evidence=factories.evidence(), nazar=nazar)
    return action, seen


async def test_unknown_action_is_rejected():
    action, _ = await validate("delete_company", {})

    assert action.problems == ("unknown action 'delete_company'",)


async def test_pay_change_comes_with_nazars_own_before_and_after():
    action, seen = await validate("adjust_shift", shift())

    assert action.valid and action.level == "changes_pay"
    assert (action.preview.before["late_minutes"], action.preview.after["late_minutes"]) == (74, 6)
    preview_request = next(request for request in seen if request.url.path.endswith("/preview"))
    item = json.loads(preview_request.content)["items"][0]
    assert item["intervals"] == [{"start_at": "2026-10-23T09:06:00+05:00", "end_at": "2026-10-23T17:49:00+05:00"}]
    assert all(request.method == "GET" or request.url.path.endswith(("/preview", "/login")) for request in seen)


async def test_interval_problems_are_caught_before_nazar_is_asked():
    naive, seen = await validate("adjust_shift", shift(start="2026-10-23T09:06:00"))
    backwards, _ = await validate("adjust_shift", shift(start="2026-10-23T18:00:00+05:00"))
    other_day, _ = await validate("adjust_shift", shift(start="2026-10-27T09:00:00+05:00", end="2026-10-27T18:00:00+05:00"))

    assert "UTC offset" in naive.problems[0] and seen == []
    assert "not after" in backwards.problems[0]
    assert "outside 2026-10-23" in other_day.problems[0]


async def test_agent_cannot_act_on_a_record_it_has_not_seen():
    action, _ = await validate("adjust_shift", shift(employee_id="99999999-9999-9999-9999-999999999999"))

    assert action.problems[0].startswith("acts on records no tool returned")


async def test_policy_guards_verdicts_and_unpaid_days():
    confirm = {"event_id": factories.EVENT, "status": "confirmed", "comment": "c"}
    void = {"employee_id": factories.EMPLOYEE, "work_date": factories.DAY, "reason": "not her check-in"}

    weak_confirm, _ = await validate("review_event", confirm, WEAK)
    strong_confirm, _ = await validate("review_event", confirm, STRONG)
    weak_void, _ = await validate("void_shift", void, WEAK)
    unsure, _ = await validate("review_event", {**confirm, "status": "insufficient_data"}, WEAK)

    assert "high confidence" in weak_confirm.problems[0]
    assert strong_confirm.valid and strong_confirm.preview is None
    assert "not paying a day" in weak_void.problems[0]
    assert unsure.valid


async def test_nazar_refusing_the_preview_rejects_the_action():
    action, _ = await validate("adjust_shift", shift(), preview_status=403)

    assert action.problems == ("NAZAR could not compute before → after: NAZAR API error 403: Request origin is required.",)
