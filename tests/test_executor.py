import json

import pytest

from nazar_agent.actions import ExecutionError, Executor, ProposalStore
from tests import factories

ADJUST = {
    "employee_id": factories.EMPLOYEE,
    "work_date": factories.DAY,
    "intervals": [{"start_at": "2026-10-23T09:06:00+05:00", "end_at": "2026-10-23T17:49:00+05:00"}],
    "reason": "Считать с реального прихода",
}
BEFORE = {field: factories.DAY_ROW[field] for field in ("day_code", "worked_minutes", "paid_minutes", "late_minutes", "early_leave_minutes")}
REVIEW = {"event_id": factories.EVENT, "status": "no_violation", "comment": "Сбой GPS"}


@pytest.fixture
def store(tmp_path):
    return ProposalStore(tmp_path / "agent.sqlite3")


def propose(store, action="adjust_shift", params=ADJUST, *, level="changes_pay", confidence="medium", verdict="inconclusive"):
    preview = {"before": BEFORE, "after": {**BEFORE, "late_minutes": 6}} if level == "changes_pay" else None
    return store.add(
        action=action, level=level, params=params, preview=preview,
        context={"confidence": confidence, "skeptic_verdict": verdict},
        card={"title": "t", "subject": {"name": "Зарина"}}, question="q", actor="nazar-agent",
    )


def writes(seen):
    return [(request.method, request.url.path.removeprefix("/api")) for request in seen if request.method in {"POST", "DELETE"} and "preview" not in request.url.path and "login" not in request.url.path]


async def test_confirm_writes_to_nazar_names_the_human_and_records_it(store):
    nazar, seen = factories.nazar()
    proposal = propose(store)

    done = await Executor(store, nazar).confirm(proposal.id, "Шерзод")

    assert done.status == "executed" and done.result == {"adjustment_id": factories.ADJUSTMENT}
    assert writes(seen) == [("POST", "/timesheet/adjustments")]
    body = json.loads(next(r for r in seen if r.url.path.endswith("/timesheet/adjustments")).content)
    assert body["assignment_id"] == factories.ASSIGNMENT
    assert body["reason"].endswith("подтвердил(а): Шерзод, через NAZAR Agent")
    events = [entry.event for entry in store.audit(proposal_id=proposal.id)]
    assert events == ["executed", "proposed"]  # newest first


async def test_a_proposal_runs_once_however_many_clicks(store):
    nazar, seen = factories.nazar()
    proposal = propose(store)
    executor = Executor(store, nazar)

    await executor.confirm(proposal.id, "Шерзод")
    with pytest.raises(ExecutionError) as second:
        await executor.confirm(proposal.id, "Шерзод")

    assert second.value.code == "wrong_status"
    assert writes(seen) == [("POST", "/timesheet/adjustments")]


async def test_refuses_when_the_day_changed_since_the_human_saw_it(store):
    day = dict(factories.DAY_ROW)
    nazar, seen = factories.nazar(day_row=day)
    proposal = propose(store)
    day["late_minutes"] = 0  # someone fixed it by hand meanwhile

    with pytest.raises(ExecutionError) as refused:
        await Executor(store, nazar).confirm(proposal.id, "Шерзод")

    assert refused.value.code == "stale"
    assert store.get(proposal.id).status == "stale"
    assert writes(seen) == []
    [entry, _] = store.audit(proposal_id=proposal.id)
    assert entry.event == "refused" and entry.details["now"]["late_minutes"] == 0


async def test_policy_is_checked_again_at_confirmation(store):
    nazar, seen = factories.nazar()
    proposal = propose(store, "void_shift", {"employee_id": factories.EMPLOYEE, "work_date": factories.DAY, "reason": "r"})

    with pytest.raises(ExecutionError) as refused:
        await Executor(store, nazar).confirm(proposal.id, "Шерзод")

    assert refused.value.code == "refused" and writes(seen) == []


async def test_nazar_refusing_the_write_is_recorded_as_failed(store):
    nazar, _ = factories.nazar(write_status=422)
    proposal = propose(store)

    with pytest.raises(ExecutionError) as failed:
        await Executor(store, nazar).confirm(proposal.id, "Шерзод")

    assert failed.value.code == "failed" and "NAZAR said no." in failed.value.message
    assert store.get(proposal.id).status == "failed"


async def test_undo_deactivates_the_adjustment(store):
    nazar, seen = factories.nazar()
    executor = Executor(store, nazar)
    proposal = propose(store)
    await executor.confirm(proposal.id, "Шерзод")

    undone = await executor.undo(proposal.id, "Шерзод")

    assert undone.status == "undone"
    assert writes(seen)[-1] == ("DELETE", f"/timesheet/adjustments/{factories.ADJUSTMENT}")


async def test_review_undo_restores_the_previous_status(store):
    nazar, seen = factories.nazar()
    executor = Executor(store, nazar)
    proposal = propose(store, "review_event", REVIEW, level="record_only")

    await executor.confirm(proposal.id, "Шерзод")
    await executor.undo(proposal.id, "Шерзод")

    reviews = [json.loads(r.content) for r in seen if r.url.path.endswith("/review")]
    assert reviews[0]["status"] == "no_violation" and reviews[0]["comment"].startswith("Сбой GPS")
    assert reviews[1] == {"status": "not_reviewed", "comment": None}


def test_disagreeing_needs_a_reason_and_is_kept(store):
    nazar, _ = factories.nazar()
    executor = Executor(store, nazar)
    proposal = propose(store)

    with pytest.raises(ExecutionError):
        executor.decline(proposal.id, "Шерзод", "  ")
    declined = executor.decline(proposal.id, "Шерзод", "У них правда нет телефонов")

    assert declined.status == "declined"
    assert store.audit(proposal_id=proposal.id)[0].details == {"reason": "У них правда нет телефонов"}


async def test_only_executed_actions_can_be_undone(store):
    nazar, _ = factories.nazar()

    with pytest.raises(ExecutionError) as error:
        await Executor(store, nazar).undo(propose(store).id, "Шерзод")

    assert error.value.code == "wrong_status"


def test_unknown_proposal(store):
    nazar, _ = factories.nazar()

    with pytest.raises(ExecutionError) as error:
        Executor(store, nazar).decline("nope", "Шерзод", "r")

    assert error.value.code == "not_found"
