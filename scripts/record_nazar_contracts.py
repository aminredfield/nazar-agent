"""Record one real answer of every NAZAR endpoint the agent calls → tests/contracts/.

Run against a NAZAR with the construction demo company — never a real
client's data. Everything this script changes it puts back at once (a review,
an adjustment, a site's settings); the one thing it leaves is an employee
created from a demo face, as the agent's list import would.

    NAZAR_BASE_URL=http://localhost:8000/api NAZAR_EMAIL=... NAZAR_PASSWORD=... NAZAR_AGENT_KEY=... \\
        python scripts/record_nazar_contracts.py --face path/to/demo-face.jpg

``--only setup`` records just the routes of setting up through the chat (a
site, a group, its schedule, members) on a site it creates and deletes again.

Tokens, passwords and e-mail addresses are redacted before saving; lists are
cut to a few items. The demo NAZAR (nazar_agent/demo) must answer with the same
fields of the same types: tests/test_contracts.py.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from nazar_agent.demo.contracts import redact

CONTRACTS = Path(__file__).resolve().parents[1] / "tests" / "contracts"
TZ = ZoneInfo("Asia/Tashkent")
KEEP = 4  # list items kept per list


class Recorder:
    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self.saved: list[str] = []

    async def call(self, name: str, method: str, path: str, *, save: bool = True, **request: Any) -> Any:
        response = await self.http.request(method, path, **request)
        body = response.json() if response.headers.get("content-type", "").startswith("application/json") else None
        expected_error = name.startswith("error_")
        if response.is_error and not expected_error:
            # Never overwrite a good recording with a failure: stop and say what NAZAR said.
            raise RuntimeError(f"{method} {_template(path)} → {response.status_code}: {response.text[:300]}")
        if save:
            record = {
                "request": f"{method} {_template(path)}",
                "status": response.status_code,
                "body": _trim(redact(body)),
            }
            (CONTRACTS / f"{name}.json").write_text(json.dumps(record, ensure_ascii=False, indent=1), encoding="utf-8")
            self.saved.append(f"{response.status_code} {method} {_template(path)} → {name}")
        if response.is_error and save and name.startswith("error_"):
            return body
        response.raise_for_status()
        return (body or {}).get("data")


def _template(path: str) -> str:
    """/objects/6d1f… → /objects/{id}: the contract is about the route, not the record."""
    parts = []
    for part in path.split("/"):
        try:
            uuid.UUID(part)
            parts.append("{id}")
        except ValueError:
            parts.append(part)
    return "/".join(parts)


def _trim(value: Any) -> Any:
    if isinstance(value, list):
        return [_trim(item) for item in value[:KEEP]]
    if isinstance(value, dict):
        return {key: _trim(item) for key, item in value.items()}
    return value


async def record_setup(rec: Recorder, employees: list[dict[str, Any]], today: date) -> None:
    """A new site with a group on it and one member — then all of it taken off and deleted again."""
    await rec.call("departments", "GET", "/departments")
    group = await rec.call("department_create", "POST", "/departments", json={"name": "Контракт: бригада", "color": "violet"})
    site = await rec.call("object_create", "POST", "/objects", json={
        "name": "Контракт: ЖК Олмазор", "address": "Ташкент, Чиланзар", "latitude": 41.2856, "longitude": 69.2034,
        "radius_meters": 100, "shift_start": "08:00", "shift_end": "17:00", "work_schedule_mode": "custom",
        "working_days": [1, 2, 3, 4, 5, 6],
    })
    try:
        await rec.call("object_schedule_preview", "POST", f"/objects/{site['id']}/schedule-preview", json={
            "schedule_mode": "custom", "schedule_type": "weekly", "shift_start": "08:00", "shift_end": "17:00",
            "working_days": [1, 2, 3, 4, 5, 6], "start_date": today.isoformat(), "days": 7,
        })
        await rec.call("object_group_set", "POST", f"/objects/{site['id']}/groups",
                       json={"department_id": group["id"], "schedule_mode": "object_default"})
        member = next(item["id"] for item in employees if item.get("is_active", True))
        await rec.call("departments_bulk_add", "POST", "/departments/bulk-add",
                       json={"employee_ids": [member], "department_ids": [group["id"]], "effective_from": today.isoformat()})
        await rec.call("object_assignments_add", "POST", f"/objects/{site['id']}/assignments",
                       json={"employee_ids": [member], "start_date": today.isoformat()}, save=False)
        await rec.call("object_groups", "GET", f"/objects/{site['id']}/groups")
        await rec.call("object_assignments_clear", "POST", f"/objects/{site['id']}/assignments",
                       json={"employee_ids": [], "end_date": today.isoformat()}, save=False)
        await rec.call("departments_bulk_remove", "POST", "/departments/bulk-remove",
                       json={"employee_ids": [member], "department_ids": [group["id"]], "effective_from": today.isoformat()})
        await rec.call("object_group_remove", "DELETE", f"/objects/{site['id']}/groups/{group['id']}")
    finally:
        await rec.call("department_delete", "DELETE", f"/departments/{group['id']}")
        await rec.call("object_delete", "DELETE", f"/objects/{site['id']}")


async def record(face: Path, only: str | None = None) -> list[str]:
    base = os.environ["NAZAR_BASE_URL"].rstrip("/")
    email, password, key = os.environ["NAZAR_EMAIL"], os.environ["NAZAR_PASSWORD"], os.environ["NAZAR_AGENT_KEY"]
    CONTRACTS.mkdir(parents=True, exist_ok=True)
    marker = {"X-Request-Origin": "nazar_agent"}

    # Cookie sign-in, as a local setup does.
    async with httpx.AsyncClient(base_url=base, headers=marker, timeout=60) as cookies:
        session = Recorder(cookies)
        await session.call("auth_login", "POST", "/auth/login", json={"email": email, "password": password}, save=only is None)
        await session.call("auth_refresh", "POST", "/auth/refresh", save=only is None)
        saved = session.saved

    async with httpx.AsyncClient(base_url=base, headers=marker, timeout=60) as http:
        rec = Recorder(http)
        signed = await rec.call(
            "ai_agent_service_session", "POST", "/ai-agent/service-session",
            json={"email": email, "password": password}, headers={"X-Agent-Key": key}, save=only is None,
        )
        http.headers["Authorization"] = f"Bearer {signed['access_token']}"
        company_id = signed["company_id"]

        today = datetime.now(TZ).date()
        if only == "setup":
            await record_setup(rec, await rec.call("employees_now", "GET", "/employees", save=False), today)
            return rec.saved
        handoff = await rec.call("ai_agent_handoff", "POST", "/ai-agent/handoff")
        await rec.call(
            "ai_agent_session", "POST", "/ai-agent/session",
            json={"handoff": handoff["url"].split("#handoff=", 1)[1]}, headers={"X-Agent-Key": key},
        )
        await rec.call(
            "ai_agent_usage", "POST", "/ai-agent/usage", headers={"X-Agent-Key": key},
            json={"company_id": company_id, "run_id": "contract", "source": "ask", "calls": [
                {"role": "dispatcher", "model": "muse-spark-1.3", "input_tokens": 1, "output_tokens": 1, "cost_usd": 0.0}
            ]},
        )

        today = datetime.now(TZ).date()
        sites = await rec.call("objects", "GET", "/objects", params={"include_inactive": "false"})
        site = sites[0]
        await rec.call("object_assignments", "GET", f"/objects/{site['id']}/assignments")
        employees = await rec.call("employees", "GET", "/employees")
        day_from = _local_start(today - timedelta(days=14))
        events = await rec.call(
            "attendance_logs", "GET", "/attendance/logs",
            params={"date_from": day_from, "date_to": _local_start(today + timedelta(days=1)), "limit": 50},
        )
        with_photo = next(item for item in events if item.get("photo_url"))
        await rec.call("attendance_event", "GET", f"/attendance/events/{with_photo['id']}")
        await rec.call("attendance_presence", "GET", "/attendance/presence", params={"status": "all", "limit": 100})
        matrix = await rec.call(
            "timesheet", "GET", "/timesheet",
            params={"date_from": (today - timedelta(days=7)).isoformat(), "date_to": today.isoformat()},
        )
        worked = next(
            row for row in matrix["days"]
            if row["day_code"] == "work" and row["work_date"] < today.isoformat() and row.get("shift_breakdown")
        )
        await rec.call(
            "timesheet_day_detail", "GET", "/timesheet/day/detail",
            params={"employee_id": worked["employee_id"], "work_date": worked["work_date"]},
        )
        await rec.call("timesheet_setup_check", "GET", "/timesheet/setup-check")
        photo = await http.get(with_photo["photo_url"])
        (CONTRACTS / "media_photo.json").write_text(json.dumps({
            "request": "GET /media/{path}", "status": photo.status_code,
            "content_type": photo.headers.get("content-type"), "bytes_over": 1000 if len(photo.content) > 1000 else 0,
        }, indent=1), encoding="utf-8")

        # Writes — each one put back.
        detail = await rec.call("attendance_event_before_review", "GET", f"/attendance/events/{with_photo['id']}", save=False)
        await rec.call("attendance_review", "POST", f"/attendance/events/{with_photo['id']}/review",
                       json={"status": "insufficient_data", "comment": "contract recording"})
        await rec.call("attendance_review_back", "POST", f"/attendance/events/{with_photo['id']}/review", save=False,
                       json={"status": detail.get("review_status") or "not_reviewed", "comment": detail.get("review_comment")})

        target = {"assignment_id": worked["shift_breakdown"][0]["assignment_id"]}
        start = datetime.fromisoformat(f"{worked['work_date']}T09:00:00+05:00")
        intervals = [{"start_at": start.isoformat(), "end_at": (start + timedelta(hours=8)).isoformat()}]
        item = {"employee_id": worked["employee_id"], "work_date": worked["work_date"], **target}
        await rec.call("timesheet_adjustments_preview", "POST", "/timesheet/adjustments/preview",
                       json={"items": [{**item, "intervals": intervals}]})
        adjustment = await rec.call("timesheet_adjustment", "POST", "/timesheet/adjustments",
                                    json={**item, "intervals": intervals, "reason": "contract recording"})
        await rec.call("timesheet_adjustment_delete", "DELETE", f"/timesheet/adjustments/{adjustment['id']}")
        void = await rec.call("timesheet_void", "POST", "/timesheet/adjustments/void", json={**item, "reason": "contract recording"})
        await rec.call("timesheet_void_delete", "DELETE", f"/timesheet/adjustments/void/{void['id']}")

        fields = ("name", "address", "latitude", "longitude", "radius_meters", "is_active", "shift_start", "shift_end",
                  "max_shift_hours", "late_grace_minutes", "early_leave_grace_minutes", "overtime_after_minutes",
                  "max_overtime_minutes", "auto_close_after_minutes", "work_schedule_mode", "working_days", "trusted_networks")
        await rec.call("object_update", "PATCH", f"/objects/{site['id']}", json={field: site.get(field) for field in fields})

        current = await rec.call("object_assignments_now", "GET", f"/objects/{site['id']}/assignments", save=False)
        assigned = [entry["employee_id"] for entry in current.get("assignments", []) if entry.get("is_active", True)]
        await rec.call("object_assignments_replace", "POST", f"/objects/{site['id']}/assignments",
                       json={"employee_ids": assigned, "start_date": today.isoformat()})

        photo_bytes = face.read_bytes()
        await rec.call("face_quality_check", "POST", "/face/quality-check", files=[("photo", ("photo.jpg", photo_bytes, "image/jpeg"))])
        await rec.call("employee_create", "POST", "/employees", data={"full_name": "Контракт Проверка"},
                       files=[("photos", ("photo.jpg", photo_bytes, "image/jpeg"))])

        # NAZAR's own errors, as the agent reads them.
        await rec.call("error_404", "GET", f"/attendance/events/{uuid.uuid4()}")
        await rec.call("error_422", "GET", "/timesheet", params={"date_from": "not-a-date", "date_to": today.isoformat()})
        assert employees, "the demo company has no employees"
        return saved + rec.saved


def _local_start(day: date) -> str:
    return datetime.combine(day, datetime.min.time(), tzinfo=TZ).isoformat()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--face", type=Path, help="a demo face photo for the employee and quality checks")
    parser.add_argument("--only", choices=["setup"], help="record only these routes, the others stay as they are")
    args = parser.parse_args()
    if args.face is None and args.only is None:
        parser.error("--face is needed to record every route")
    for line in asyncio.run(record(args.face, args.only)):
        print(line)


if __name__ == "__main__":
    main()
