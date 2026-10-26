"""Read-only tools over NAZAR attendance data.

Each tool returns a compact projection, not the raw API response: the model
gets the fields it reasons about, and every record keeps its NAZAR id so a
claim in the final answer can be traced back to it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID
from zoneinfo import ZoneInfo

from pydantic import BaseModel, Field, model_validator

from nazar_agent.engine import Tool, ToolExecutionError
from nazar_agent.nazar import NazarApiError, NazarClient

# NAZAR companies work in Tashkent time; a "day" in a question means a local day.
COMPANY_TZ = ZoneInfo("Asia/Tashkent")
MAX_RANGE_DAYS = 31
MAX_EVENTS = 200


class ListObjectsParams(BaseModel):
    include_inactive: bool = Field(description="Also return archived objects.")


class AttendanceEventsParams(BaseModel):
    date_from: date = Field(description="First local day, YYYY-MM-DD.")
    date_to: date = Field(description=f"Last local day, YYYY-MM-DD. At most {MAX_RANGE_DAYS} days after date_from.")
    object_id: UUID | None = Field(description="Only this object, or null for all objects.")
    employee_id: UUID | None = Field(description="Only this employee, or null for everyone.")
    suspicious_only: bool = Field(description="Only events flagged by anti-fraud checks.")
    limit: int = Field(ge=1, le=MAX_EVENTS, description=f"How many events to return, 1–{MAX_EVENTS}.")

    @model_validator(mode="after")
    def _check_range(self) -> AttendanceEventsParams:
        if self.date_to < self.date_from:
            raise ValueError("date_to is before date_from")
        if (self.date_to - self.date_from).days > MAX_RANGE_DAYS:
            raise ValueError(f"the range is longer than {MAX_RANGE_DAYS} days")
        return self


class EventDetailParams(BaseModel):
    event_id: UUID = Field(description="Attendance event id from get_attendance_events.")


class PresenceTodayParams(BaseModel):
    status: Literal["all", "in", "out", "absent"] = Field(
        description="all; in — on site now; out — checked out today; absent — no check-in today."
    )


def attendance_tools(nazar: NazarClient) -> tuple[Tool, ...]:
    async def list_objects(params: ListObjectsParams) -> list[dict[str, Any]]:
        objects = await _call(nazar.get("/objects", {"include_inactive": params.include_inactive}))
        return [_object_view(item) for item in objects]

    async def get_attendance_events(params: AttendanceEventsParams) -> dict[str, Any]:
        events = await _call(
            nazar.get(
                "/attendance/logs",
                {
                    "date_from": _local_day_start(params.date_from).isoformat(),
                    "date_to": _local_day_start(params.date_to + timedelta(days=1)).isoformat(),
                    "object_id": _str_or_none(params.object_id),
                    "employee_id": _str_or_none(params.employee_id),
                    "suspicious_only": params.suspicious_only,
                    "limit": params.limit,
                },
            )
        )
        return {
            "events": [_event_view(item) for item in events],
            # The model must know when it saw only part of the period.
            "truncated": len(events) >= params.limit,
        }

    async def get_event_detail(params: EventDetailParams) -> dict[str, Any]:
        detail = await _call(nazar.get(f"/attendance/events/{params.event_id}"))
        return _event_detail_view(detail)

    async def get_presence_today(params: PresenceTodayParams) -> dict[str, Any]:
        page = await _call(
            nazar.get("/attendance/presence", {"status": params.status, "limit": 100})
        )
        return _presence_view(page)

    return (
        Tool(
            name="list_objects",
            description="List the company's work sites (objects) with geofence radius and schedule mode.",
            params=ListObjectsParams,
            handler=list_objects,
        ),
        Tool(
            name="get_attendance_events",
            description=(
                "Check-in and check-out events for a period, newest first, with anti-fraud flags, "
                "GPS accuracy and review status. Filter by object, employee or suspicious only."
            ),
            params=AttendanceEventsParams,
            handler=get_attendance_events,
        ),
        Tool(
            name="get_event_detail",
            description=(
                "Full detail of one event: risk score, distance to the site, location samples, "
                "network context, the previous event and the admin review."
            ),
            params=EventDetailParams,
            handler=get_event_detail,
        ),
        Tool(
            name="get_presence_today",
            description="Who is on site, has left or has not arrived today, with schedule-aware counters.",
            params=PresenceTodayParams,
            handler=get_presence_today,
        ),
    )


async def _call(request: Any) -> Any:
    try:
        return await request
    except NazarApiError as exc:
        raise ToolExecutionError(f"NAZAR API error {exc.status_code}: {exc.message}") from exc


def _local_day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=COMPANY_TZ)


def _str_or_none(value: UUID | None) -> str | None:
    return str(value) if value is not None else None


def _local_time(value: str | None) -> str | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(COMPANY_TZ).isoformat(timespec="seconds")


def _object_view(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "name": item["name"],
        "address": item.get("address"),
        "radius_meters": item.get("radius_meters"),
        "is_active": item.get("is_active"),
        "schedule_mode": item.get("work_schedule_mode"),
        "shift": [item.get("shift_start"), item.get("shift_end")],
        "assigned_employees": item.get("assigned_employee_count"),
    }


def _event_view(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": item["id"],
        "type": item["event_type"],
        "at": _local_time(item.get("received_at_server")),
        "employee_id": item.get("employee_id"),
        "employee_name": item.get("employee_name"),
        "object_id": item.get("object_id"),
        "object_name": item.get("object_name"),
        "face_matched": item.get("matched"),
        "inside_geofence": item.get("inside_geofence"),
        "gps_accuracy_m": item.get("accuracy_meters"),
        "risk_flags": item.get("risk_flags", []),
        "suspicious_flags": item.get("suspicious_flags", []),
        "review_status": item.get("review_status"),
        "has_photo": bool(item.get("photo_url")),
    }


def _event_detail_view(item: dict[str, Any]) -> dict[str, Any]:
    previous = item.get("previous_event")
    return {
        **_event_view(item),
        "risk_score": item.get("risk_score"),
        "risk_level": item.get("risk_level"),
        "distance_to_site_m": item.get("distance_to_object_meters"),
        "captured_at_client": _local_time(item.get("captured_at_client")),
        "location_samples": len(item.get("location_samples") or []),
        "network": item.get("network_context") or {},
        "previous_event": None
        if previous is None
        else {
            "id": previous["id"],
            "type": previous["event_type"],
            "object_name": previous.get("object_name"),
            "at": _local_time(previous.get("received_at_server")),
            "distance_m": previous.get("distance_meters"),
        },
        "review_comment": item.get("review_comment"),
        "reviewed_by": item.get("reviewed_by_name"),
    }


def _presence_view(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": page.get("summary", {}),
        "objects": page.get("objects", []),
        "employees": [
            {
                "employee_id": entry["employee"]["id"],
                "name": entry["employee"]["full_name"],
                "status": entry.get("status"),
                "object_id": entry.get("object_id"),
                "object_name": entry.get("object_name"),
                "last_event_at": _local_time(entry.get("last_event_at")),
            }
            for entry in page.get("items", [])
        ],
        "total": page.get("total"),
    }
