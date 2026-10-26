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
    return AnalystReport.model_validate({"summary": "s", "data_gaps": [], "findings": list(findings)})


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


def nazar(preview_status: int = 200) -> tuple[NazarClient, list[httpx.Request]]:
    """A NAZAR that answers the timesheet and its preview; returns the requests it saw."""
    seen: list[httpx.Request] = []

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/auth/login"):
            return httpx.Response(200, json={"success": True, "data": {}})
        if path.endswith("/timesheet"):
            return httpx.Response(200, json={"success": True, "data": {"days": [DAY_ROW]}})
        if path.endswith("/timesheet/adjustments/preview"):
            if preview_status != 200:
                return httpx.Response(
                    preview_status,
                    json={"success": False, "error": {"code": "INVALID_ORIGIN", "message": "Request origin is required."}},
                )
            return httpx.Response(200, json={"success": True, "data": [{**DAY_ROW, "worked_minutes": 523, "paid_minutes": 523, "late_minutes": 6}]})
        return httpx.Response(404, json={"success": False, "error": {"code": "NOT_FOUND", "message": path}})

    client = NazarClient(base_url="http://nazar.test/api", email="a", password="b", transport=httpx.MockTransport(route))
    return client, seen
