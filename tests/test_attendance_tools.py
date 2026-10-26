from datetime import date

import httpx
import pytest
from pydantic import ValidationError

from nazar_agent.engine import ToolExecutionError
from nazar_agent.nazar import NazarClient
from nazar_agent.tools import attendance_tools
from nazar_agent.tools.attendance import AttendanceEventsParams, EventDetailParams

EVENT = {
    "id": "e1",
    "event_type": "check_in",
    "matched": True,
    "similarity": 0.81,
    "threshold": 0.5,
    "inside_geofence": True,
    "employee_id": "p1",
    "employee_name": "Алишер Иванов",
    "employee_external_id": None,
    "object_id": "o1",
    "object_name": "Чиланзар",
    "latitude": 41.28,
    "longitude": 69.2,
    "accuracy_meters": 12.0,
    "request_ip": "10.0.0.1",
    "risk_flags": ["repeated_exact_location"],
    "suspicious_flags": [],
    "review_status": "not_reviewed",
    "photo_url": "/media/x.jpg",
    "received_at_server": "2026-10-26T03:05:00+00:00",
}


def client_with(handler) -> NazarClient:
    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"success": True, "data": {}})
        return handler(request)

    return NazarClient(base_url="http://nazar.test/api", email="a", password="b", transport=httpx.MockTransport(route))


def tool(client: NazarClient, name: str):
    return next(item for item in attendance_tools(client) if item.name == name)


def params(**overrides) -> AttendanceEventsParams:
    values = {
        "date_from": "2026-10-26",
        "date_to": "2026-10-26",
        "object_id": None,
        "employee_id": None,
        "suspicious_only": False,
        "limit": 1,
    }
    return AttendanceEventsParams(**{**values, **overrides})


async def test_events_use_local_day_bounds_and_compact_view():
    seen = {}

    def logs(request):
        seen.update(request.url.params)
        return httpx.Response(200, json={"success": True, "data": [EVENT]})

    result = await tool(client_with(logs), "get_attendance_events").handler(params())

    assert seen["date_from"] == "2026-10-26T00:00:00+05:00"
    assert seen["date_to"] == "2026-10-27T00:00:00+05:00"
    assert "object_id" not in seen
    event = result["events"][0]
    assert event["at"] == "2026-10-26T08:05:00+05:00"
    assert event["flags"] == [
        {"code": "repeated_exact_location", "meaning": "exactly the same coordinates as an earlier check-in"}
    ]
    assert event["has_photo"] is True
    assert "request_ip" not in event and "latitude" not in event
    assert result["truncated"] is True  # limit 1, one event came back


def test_period_must_be_ordered_and_bounded():
    with pytest.raises(ValidationError, match="before"):
        params(date_from=date(2026, 10, 26), date_to=date(2026, 10, 25))
    with pytest.raises(ValidationError, match="longer"):
        params(date_from=date(2026, 8, 1), date_to=date(2026, 10, 26))


async def test_detail_names_distances_unambiguously():
    detail_body = {
        **EVENT,
        "distance_to_object_meters": 6600.0,
        "location_samples": [],
        "network_context": {},
        "risk_score": 70,
        "risk_level": "high",
        "captured_at_client": None,
        "previous_event": {
            "id": "e0",
            "event_type": "check_in",
            "object_name": "Чиланзар",
            "latitude": 41.3,
            "longitude": 69.2,
            "received_at_server": "2026-10-26T02:00:00+00:00",
            "distance_meters": 6600.0,
        },
        "admin_notified_at": None,
        "review_comment": None,
        "reviewed_at": None,
        "reviewed_by_name": None,
    }

    def detail(request):
        return httpx.Response(200, json={"success": True, "data": detail_body})

    result = await tool(client_with(detail), "get_event_detail").handler(
        EventDetailParams(event_id="00000000-0000-0000-0000-000000000001")
    )

    assert result["distance_to_site_m"] == 6600.0
    assert result["previous_event"]["distance_between_check_ins_m"] == 6600.0
    assert "distance_m" not in result["previous_event"]


async def test_api_error_becomes_tool_error_for_the_model():
    def missing(request):
        return httpx.Response(404, json={"success": False, "error": {"code": "NOT_FOUND", "message": "Event not found."}})

    detail = tool(client_with(missing), "get_event_detail")

    with pytest.raises(ToolExecutionError, match="404: Event not found."):
        await detail.handler(EventDetailParams(event_id="00000000-0000-0000-0000-000000000001"))
