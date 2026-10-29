"""Every action the agent proposes, end to end on the demo NAZAR: preview → apply → as previewed → undo → as before."""

from datetime import UTC, datetime

import httpx
import pytest

from nazar_agent.actions import Executor, ProposalStore
from nazar_agent.demo.server import DEMO_AGENT_KEY, DEMO_EMAIL, DEMO_PASSWORD, DemoNazar, create_app
from nazar_agent.demo.world import TZ, build_world
from nazar_agent.nazar import NazarClient
from nazar_agent.recommendations.catalog import CATALOG

NOW = datetime(2026, 10, 28, 15, 30, tzinfo=TZ)
DAY_FIELDS = ("day_code", "worked_minutes", "paid_minutes", "late_minutes", "early_leave_minutes")


@pytest.fixture
async def demo(tmp_path):
    state = DemoNazar(build_world(NOW), clock=lambda: NOW.astimezone(UTC))
    nazar = NazarClient(
        base_url="http://demo-nazar", email=DEMO_EMAIL, password=DEMO_PASSWORD, agent_key=DEMO_AGENT_KEY,
        transport=httpx.ASGITransport(app=create_app(state)),
    )
    yield state, nazar, ProposalStore(tmp_path / "agent.sqlite3")
    await nazar.aclose()


def person(state: DemoNazar, name: str) -> str:
    return next(item.id for item in state.world.people.values() if item.name == name)


async def day(nazar: NazarClient, employee_id: str, work_date: str) -> dict:
    detail = await nazar.get("/timesheet/day/detail", {"employee_id": employee_id, "work_date": work_date})
    return {field: detail["day"][field] for field in DAY_FIELDS}


async def proposed(store: ProposalStore, nazar: NazarClient, action: str, values: dict):
    """As the agent stores a proposal: validated params and the preview NAZAR counted."""
    spec = CATALOG[action]
    params = spec.params.model_validate(values)
    preview = await spec.preview(nazar, params) if spec.preview else None
    proposal = store.add(
        action=action, level=spec.level, params=params.model_dump(mode="json"),
        preview={"before": preview.before, "after": preview.after} if preview else None,
        context={"confidence": "high", "skeptic_verdict": "concern_supported"},
        card={"title": "t", "subject": {"name": "x"}}, question="q", actor="nazar-agent",
    )
    return proposal, preview


async def test_an_adjusted_day_is_what_the_preview_said_and_undo_puts_it_back(demo):
    state, nazar, store = demo
    azamat = person(state, "Azamat Normatov")
    before = await day(nazar, azamat, "2026-10-26")
    proposal, preview = await proposed(store, nazar, "adjust_shift", {
        "employee_id": azamat, "work_date": "2026-10-26", "reason": "forgot to check out",
        "intervals": [{"start_at": "2026-10-26T08:00:00+05:00", "end_at": "2026-10-26T18:00:00+05:00"}],
    })
    executor = Executor(store, nazar)

    await executor.confirm(proposal.id, "Владелец")
    applied = await day(nazar, azamat, "2026-10-26")
    await executor.undo(proposal.id, "Владелец")

    assert preview.before == before and preview.after == applied
    assert applied["worked_minutes"] == 600 and applied != before
    assert await day(nazar, azamat, "2026-10-26") == before


async def test_a_dead_souls_day_is_not_paid_and_undo_pays_it_again(demo):
    state, nazar, store = demo
    timur = person(state, "Timur Hasanov")
    before = await day(nazar, timur, "2026-10-27")
    proposal, preview = await proposed(store, nazar, "void_shift", {
        "employee_id": timur, "work_date": "2026-10-27", "reason": "left ten days ago",
    })
    executor = Executor(store, nazar)

    await executor.confirm(proposal.id, "Владелец")
    voided = await day(nazar, timur, "2026-10-27")
    await executor.undo(proposal.id, "Владелец")

    assert before["paid_minutes"] > 0 and voided["paid_minutes"] == 0
    assert preview.after == voided
    assert await day(nazar, timur, "2026-10-27") == before


async def test_the_wrong_schedule_is_fixed_from_today_and_undo_restores_it(demo):
    state, nazar, store = demo
    site = next(item for item in state.world.sites.values() if item.key == "yashnobod")
    zuhra = person(state, "Zuhra Nabieva")
    proposal, preview = await proposed(store, nazar, "change_schedule", {
        "object_id": site.id, "shift_start": "09:00", "shift_end": "19:00", "reason": "the crew works 09-19",
    })
    executor = Executor(store, nazar)

    await executor.confirm(proposal.id, "Владелец")
    changed = next(item for item in await nazar.get("/objects") if item["id"] == site.id)
    yesterday = await day(nazar, zuhra, "2026-10-27")
    await executor.undo(proposal.id, "Владелец")
    restored = next(item for item in await nazar.get("/objects") if item["id"] == site.id)

    assert (preview.before["shift_start"], preview.after["shift_start"]) == ("08:00", "09:00")
    assert (changed["shift_start"], changed["shift_end"]) == ("09:00", "19:00")
    assert yesterday["late_minutes"] > 45  # the past stays as it was counted
    assert (restored["shift_start"], restored["shift_end"]) == ("08:00", "18:00")


async def test_a_review_is_recorded_and_undo_restores_the_previous_one(demo):
    state, nazar, store = demo
    timur = person(state, "Timur Hasanov")
    event = next(item for item in reversed(state.world.events) if item.employee_id == timur)
    proposal, _ = await proposed(store, nazar, "review_event", {
        "event_id": event.id, "status": "insufficient_data", "comment": "a photo of a screen",
    })
    executor = Executor(store, nazar)

    await executor.confirm(proposal.id, "Владелец")
    reviewed = await nazar.get(f"/attendance/events/{event.id}")
    await executor.undo(proposal.id, "Владелец")
    restored = await nazar.get(f"/attendance/events/{event.id}")

    assert reviewed["review_status"] == "insufficient_data" and "a photo of a screen" in reviewed["review_comment"]
    assert restored["review_status"] == "not_reviewed" and restored["review_comment"] is None
