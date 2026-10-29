"""Shared test data: a verified finding about a check-in far from the shop."""

from __future__ import annotations

import httpx

from nazar_agent.engine import ToolStep
from nazar_agent.findings import AnalystReport, Evidence
from nazar_agent.nazar import NazarClient

EMPLOYEE = "11111111-1111-1111-1111-111111111111"
EVENT = "22222222-2222-2222-2222-222222222222"
DAY = "2026-10-23"

STEPS = (
    ToolStep(
        call_id="c1",
        name="get_attendance_events",
        arguments={},
        output={"events": [{
            "id": EVENT,
            "employee_id": EMPLOYEE,
            "employee_name": "Зарина Хакимова",
            "at": "2026-10-23T10:14:05+05:00",
            "gps_accuracy_m": 15.0,
            "flags": [{"code": "mock_location", "meaning": "fake location"}],
        }]},
        error=None,
        duration_ms=1,
    ),
)


def finding(**overrides) -> dict:
    return {
        "title": "Повторная отметка с подменой геопозиции",
        "subject_kind": "employee",
        "concern": "people",
        "subject_id": EMPLOYEE,
        "subject_name": "Зарина Хакимова",
        "facts": [{
            "text": "В 10:14 отметка с признаком подмены геопозиции",
            "evidence": [{"kind": "event", "id": EVENT}],
            "claims": [{"ref": {"kind": "event", "id": EVENT}, "field": "at", "value": "10:14"}],
        }],
        "hypothesis": "Отметку сделали не с места работы.",
        "alternatives": ["Включено приложение, меняющее геопозицию"],
        "confidence": "high",
        **overrides,
    }


def report(*findings: dict) -> AnalystReport:
    return AnalystReport.model_validate({"answer": [], "summary": "s", "data_gaps": [], "findings": list(findings)})


def evidence() -> Evidence:
    return Evidence.from_steps(STEPS)


ASSIGNMENT = "33333333-3333-3333-3333-333333333333"
DAY_ROW = {
    "employee_id": EMPLOYEE,
    "work_date": DAY,
    "object_id": "44444444-4444-4444-4444-444444444444",
    "shift_breakdown": [{"assignment_id": ASSIGNMENT, "intervals": []}],
    "day_code": "work",
    "worked_minutes": 455,
    "paid_minutes": 455,
    "late_minutes": 74,
    "early_leave_minutes": 10,
}


ADJUSTMENT = "55555555-5555-5555-5555-555555555555"
SITE = "66666666-6666-6666-6666-666666666666"
NEW_EMPLOYEE = "77777777-7777-7777-7777-777777777777"
SITES = [
    {"id": SITE, "name": "Магазин Чиланзар", "address": "Чиланзар, 9 квартал"},
    {"id": "88888888-8888-8888-8888-888888888888", "name": "Магазин Юнусабад", "address": "Юнусабад"},
]
EMPLOYEES = [{"id": EMPLOYEE, "full_name": "Зарина Хакимова"}]
# What NAZAR's quality check says; tests replace it to simulate a bad photo.
PHOTO_CHECK: dict = {"face_detected": True, "face_count": 1, "blocking_reasons": [], "warnings": []}


def ok(data):
    return httpx.Response(200, json={"success": True, "data": data})


def nazar_routes(preview_status: int = 200, day_row: dict | None = None, write_status: int = 200):
    """Handler of a fake NAZAR: timesheet, preview, adjustments, event review.

    ``day_row`` is a mutable dict the test may change, to simulate the day
    changing in NAZAR between proposal and confirmation.
    """
    day = day_row if day_row is not None else dict(DAY_ROW)
    seen: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path, method = request.url.path, request.method
        if path.endswith("/auth/login"):
            return ok({})
        if path.endswith("/timesheet"):
            return ok({"days": [day]})
        if path.endswith("/timesheet/adjustments/preview"):
            if preview_status != 200:
                return httpx.Response(
                    preview_status,
                    json={"success": False, "error": {"code": "INVALID_ORIGIN", "message": "Request origin is required."}},
                )
            return ok([{**day, "worked_minutes": 523, "paid_minutes": 523, "late_minutes": 6}])
        if write_status != 200 and method in {"POST", "DELETE"}:
            return httpx.Response(write_status, json={"success": False, "error": {"code": "X", "message": "NAZAR said no."}})
        if path.endswith("/timesheet/adjustments") and method == "POST":
            return ok({"id": ADJUSTMENT})
        if path.endswith(f"/timesheet/adjustments/{ADJUSTMENT}") and method == "DELETE":
            return ok(None)
        if path.endswith(f"/attendance/events/{EVENT}") and method == "GET":
            return ok({"id": EVENT, "review_status": "not_reviewed", "review_comment": None})
        if path.endswith(f"/attendance/events/{EVENT}/review") and method == "POST":
            return ok({"id": EVENT})
        if path.endswith("/face/quality-check"):
            if PHOTO_CHECK is None:  # what NAZAR does when there is no face at all
                return httpx.Response(422, json={"success": False, "error": {"code": "VALIDATION", "message": "Лицо не найдено на изображении."}})
            return ok(PHOTO_CHECK)
        if path.endswith("/employees") and method == "POST":
            return ok({"id": NEW_EMPLOYEE, "full_name": "x"})
        if path.endswith("/employees") and method == "GET":
            return ok(EMPLOYEES)
        if path.endswith("/objects") and method == "GET":
            return ok(SITES)
        if path.endswith(f"/objects/{SITE}/assignments"):
            return ok({"object_id": SITE, "assignments": [{"employee_id": EMPLOYEE, "is_active": True}]})
        return httpx.Response(404, json={"success": False, "error": {"code": "NOT_FOUND", "message": path}})

    return route, seen


def nazar(preview_status: int = 200, day_row: dict | None = None, write_status: int = 200) -> tuple[NazarClient, list[httpx.Request]]:
    """A fake NAZAR client; returns the requests it saw."""
    route, seen = nazar_routes(preview_status, day_row, write_status)
    client = NazarClient(base_url="http://nazar.test/api", email="a", password="b", transport=httpx.MockTransport(route))
    return client, seen
