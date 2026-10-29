"""Applying a setup plan step by step, and undoing it in reverse order.

Each step is done in NAZAR with the rights of whoever applies it, written to
the audit trail with the sentence the owner saw, and what it needs to be
undone is kept: the new site's id, the group's previous schedule there, who
was not in the group before, the list a site had. A step that fails stops
the plan — what was done stays done and listed, and "Undo all" takes it
back. A move to another site has no clean undo in NAZAR: undo leaves it, and
says so.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from nazar_agent.actions.store import ProposalStore, Transaction
from nazar_agent.engine import ToolExecutionError
from nazar_agent.nazar import NazarClient
from nazar_agent.onboarding import DraftStore
from nazar_agent.recommendations.catalog import schedule_of, site_by_id, write_schedule
from nazar_agent.setup.plan import NEW_GROUP, NEW_SITE, Step
from nazar_agent.tools.common import call_nazar

RunStatus = Literal["applying", "applied", "failed", "undoing", "undone"]
GROUP_COLOR = "violet"
_SCHEMA = """
CREATE TABLE IF NOT EXISTS setup_runs (
    id TEXT PRIMARY KEY,
    draft_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    steps TEXT NOT NULL,
    done TEXT NOT NULL,
    error TEXT
);
"""


class SetupRefused(Exception):
    """The plan cannot be applied or undone now; the message says why."""


@dataclass(frozen=True, slots=True)
class Run:
    id: str
    draft_id: str
    status: RunStatus
    steps: tuple[dict[str, Any], ...]  # kind, params, text — as the owner saw them
    done: tuple[dict[str, Any], ...]  # kind, text, result, undone
    error: str | None

    @property
    def audit_id(self) -> str:
        return f"setup:{self.id}"


class SetupStore:
    """Applied plans, next to the proposals and the audit trail."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        with Transaction.open(self._path) as connection:
            connection.execute(_SCHEMA)

    def start(self, draft_id: str, steps: list[dict[str, Any]]) -> Run:
        """A new run; refused if this draft was applied already or is being applied."""
        now = _now()
        run_id = uuid.uuid4().hex[:12]
        with Transaction.open(self._path) as connection:
            busy = connection.execute(
                "SELECT 1 FROM setup_runs WHERE draft_id = ? AND status IN ('applying', 'applied', 'failed', 'undoing')",
                (draft_id,),
            ).fetchone()
            if busy:
                raise SetupRefused("This plan has been applied already.")
            connection.execute(
                "INSERT INTO setup_runs VALUES (?, ?, ?, ?, 'applying', ?, '[]', NULL)",
                (run_id, draft_id, now, now, json.dumps(steps, ensure_ascii=False)),
            )
        return self.get(run_id)

    def get(self, run_id: str) -> Run:
        with Transaction.open(self._path) as connection:
            row = connection.execute("SELECT * FROM setup_runs WHERE id = ?", (run_id,)).fetchone()
        if row is None:
            raise KeyError(run_id)
        return Run(row["id"], row["draft_id"], row["status"], tuple(json.loads(row["steps"])), tuple(json.loads(row["done"])), row["error"])

    def save(self, run: Run) -> Run:
        with Transaction.open(self._path) as connection:
            connection.execute(
                "UPDATE setup_runs SET status = ?, done = ?, error = ?, updated_at = ? WHERE id = ?",
                (run.status, json.dumps(list(run.done), ensure_ascii=False, default=str), run.error, _now(), run.id),
            )
        return run

    def claim(self, run_id: str, *, expected: tuple[RunStatus, ...], to: RunStatus) -> Run | None:
        """Move a run from one of ``expected`` to ``to`` atomically: a double click undoes once."""
        with Transaction.open(self._path) as connection:
            marks = ",".join("?" for _ in expected)
            cursor = connection.execute(
                f"UPDATE setup_runs SET status = ?, updated_at = ? WHERE id = ? AND status IN ({marks})",
                (to, _now(), run_id, *expected),
            )
        return self.get(run_id) if cursor.rowcount == 1 else None


async def apply(
    steps: list[Step], texts: list[str], *, draft_id: str, nazar: NazarClient, store: SetupStore,
    audit: ProposalStore, drafts: DraftStore, actor: str, today: date,
) -> Run:
    """Do the steps in order; stop at the first that fails. Never raises for a failed step: the run says it."""
    run = store.start(draft_id, [{"kind": step.kind, "params": step.params, "text": text} for step, text in zip(steps, texts, strict=True)])
    made: dict[str, str] = {}  # "$site" / "$group" → the id NAZAR gave
    done: list[dict[str, Any]] = []
    for step, text in zip(steps, texts, strict=True):
        params = {key: made.get(value, value) if isinstance(value, str) else value for key, value in step.params.items()}
        try:
            result = await _do(step.kind, params, nazar, drafts, actor, today)
        except ToolExecutionError as exc:
            audit.record(run.audit_id, "setup_failed", actor, {"kind": step.kind, "text": text, "error": str(exc)})
            return store.save(replace(run, status="failed", done=tuple(done), error=f"{text} — {exc}"))
        if step.kind == "create_site":
            made[NEW_SITE] = result["object_id"]
        if step.kind == "create_group":
            made[NEW_GROUP] = result["department_id"]
        done.append({"kind": step.kind, "params": params, "text": text, "result": result, "undone": False})
        audit.record(run.audit_id, "setup_step", actor, {"kind": step.kind, "text": text})
        run = store.save(replace(run, done=tuple(done)))
    return store.save(replace(run, status="applied", done=tuple(done)))


async def undo(run_id: str, *, nazar: NazarClient, store: SetupStore, audit: ProposalStore, drafts: DraftStore, actor: str) -> Run:
    """Take the done steps back, last first. A step that cannot be undone is left and said; the rest go on."""
    run = store.claim(run_id, expected=("applied", "failed"), to="undoing")
    if run is None:
        raise SetupRefused("This plan is not applied, or is being undone already.")
    done = list(run.done)
    problems: list[str] = []
    for index in reversed(range(len(done))):
        item = done[index]
        if item["undone"]:
            continue
        try:
            kept = await _undo(item["kind"], item["params"], item["result"], nazar, drafts, actor)
        except ToolExecutionError as exc:
            problems.append(f"{item['text']} — {exc}")
            continue
        if kept:
            problems.append(f"{item['text']} — {kept}")
            continue
        done[index] = {**item, "undone": True}
        audit.record(run.audit_id, "setup_undone", actor, {"kind": item["kind"], "text": item["text"]})
    return store.save(replace(run, status="undone", done=tuple(done), error="; ".join(problems) or None))


# --- One step each way -----------------------------------------------------------------


async def _do(kind: str, params: dict[str, Any], nazar: NazarClient, drafts: DraftStore, actor: str, today: date) -> dict[str, Any]:
    if kind == "create_site":
        site = await call_nazar(nazar.post("/objects", params))
        return {"object_id": site["id"]}
    if kind == "site_schedule":
        schedule = {**{key: params[key] for key in ("shift_start", "shift_end", "working_days")}, "work_schedule_mode": "custom"}
        return {"previous": await write_schedule(nazar, params["object_id"], schedule), "written": schedule}
    if kind == "create_group":
        group = await call_nazar(nazar.post("/departments", {"name": params["name"], "color": GROUP_COLOR}))
        return {"department_id": group["id"]}
    if kind == "group_on_site":
        before = await call_nazar(nazar.get(f"/objects/{params['object_id']}/groups"))
        previous = next((item for item in before.get("groups", []) if item["department_id"] == params["department_id"]), None)
        await call_nazar(nazar.post(f"/objects/{params['object_id']}/groups", _group_body(params)))
        return {"previous": _group_body(previous) if previous else None}
    if kind == "add_to_group":
        employees = await call_nazar(nazar.get("/employees"))
        already = {item["id"] for item in employees if any(group["id"] == params["department_id"] for group in item.get("departments") or [])}
        added = [person for person in params["employee_ids"] if person not in already]
        if added:
            await call_nazar(nazar.post("/departments/bulk-add", {
                "employee_ids": added, "department_ids": [params["department_id"]], "effective_from": today.isoformat(),
            }))
        return {"added": added}
    if kind == "assign":
        current = await _assigned(nazar, params["object_id"])
        wanted = list(dict.fromkeys([*current, *params["employee_ids"]]))
        await call_nazar(nazar.post(f"/objects/{params['object_id']}/assignments", {"employee_ids": wanted, "start_date": params["start_date"]}))
        return {"previous": current}
    if kind == "transfer":
        moved = await call_nazar(nazar.post("/assignments/transfer", {
            key: params[key] for key in ("employee_id", "from_object_id", "to_object_id", "from_date")
        }))
        return {"assignment_id": moved.get("id")}
    if kind == "wait_for_photo":
        site = await site_by_id(nazar, params["object_id"])
        made = drafts.add_many([(name, site["id"], site["name"]) for name in params["names"]], None, actor)
        return {"draft_ids": [item.id for item in made]}
    raise ToolExecutionError(f"unknown step {kind}")


async def _undo(
    kind: str, params: dict[str, Any], result: dict[str, Any], nazar: NazarClient, drafts: DraftStore, actor: str
) -> str | None:
    """None when undone; else why it was left."""
    if kind == "create_site":
        await call_nazar(nazar.delete(f"/objects/{result['object_id']}"))
    elif kind == "site_schedule":
        if schedule_of(await site_by_id(nazar, params["object_id"])) != result["written"]:
            # Someone changed it after the plan: putting ours back would erase their edit.
            return "the site's schedule was changed after this; change it back by hand in NAZAR"
        await write_schedule(nazar, params["object_id"], result["previous"])
    elif kind == "create_group":
        await call_nazar(nazar.delete(f"/departments/{result['department_id']}"))
    elif kind == "group_on_site":
        if result["previous"] is None:
            await call_nazar(nazar.delete(f"/objects/{params['object_id']}/groups/{params['department_id']}"))
        else:
            await call_nazar(nazar.post(f"/objects/{params['object_id']}/groups", result["previous"]))
    elif kind == "add_to_group" and result["added"]:
        await call_nazar(nazar.post("/departments/bulk-remove", {
            "employee_ids": result["added"], "department_ids": [params["department_id"]],
        }))
    elif kind == "assign":
        await call_nazar(nazar.post(f"/objects/{params['object_id']}/assignments", {"employee_ids": result["previous"]}))
    elif kind == "transfer":
        return "a move is undone by hand in NAZAR"
    elif kind == "wait_for_photo":
        for draft_id in result["draft_ids"]:
            drafts.discard(draft_id, actor)  # someone already photographed stays: they are an employee now
    return None


def _group_body(group: dict[str, Any]) -> dict[str, Any]:
    keys = ("department_id", "schedule_mode", "shift_start", "shift_end", "working_days")
    return {key: group.get(key) for key in keys if group.get(key) is not None}


async def _assigned(nazar: NazarClient, object_id: str) -> list[str]:
    listed = await call_nazar(nazar.get(f"/objects/{object_id}/assignments"))
    return [item["employee_id"] for item in listed.get("assignments", []) if item.get("is_active", True)]


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
