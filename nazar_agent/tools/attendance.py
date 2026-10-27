"""Read-only tools over NAZAR attendance data.

Each tool returns a compact projection, not the raw API response: the model
gets the fields it reasons about, and every record keeps its NAZAR id so a
claim in the final answer can be traced back to it.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
from typing import Any, Literal
from uuid import UUID

from pydantic import Field, model_validator

from nazar_agent.engine import Tool, ToolParams
from nazar_agent.nazar import NazarClient
from nazar_agent.tools.common import COMPANY_TZ, call_nazar, local_time

MAX_RANGE_DAYS = 31
MAX_EVENTS = 200
PAGE_SIZE = 500  # NAZAR's own maximum for /attendance/logs
MAX_SCANNED_EVENTS = 5_000
MAX_SHARED_PHONES = 10
# A check-in this soon after another person's on the same phone looks like
# one person checking in others, honestly or not.
BURST_MINUTES = 10

# What NAZAR's anti-fraud flags mean (backend/app/core/risk_flags.py), so the
# model explains them in words instead of repeating codes to a business owner.
FLAG_MEANINGS = {
    "mock_location": "the phone reported a fake (mocked) location; NAZAR blocks such check-ins",
    "accuracy_too_low": "GPS accuracy too coarse to trust the position",
    "impossible_speed": "moved between two check-ins faster than physically possible",
    "session_impossible_speed": "impossible movement speed within one check-in session",
    "device_reported_impossible_speed": "the phone itself reported an impossible speed",
    "static_high_accuracy_location": "suspiciously still and precise coordinates",
    "session_stale_location": "coordinates did not change during the session, may be stale",
    "repeated_exact_location": "exactly the same coordinates as an earlier check-in",
    "location_signal_mismatch": "two independent signals disagree: IP location far from GPS, or a point exactly at the site centre",
    "network_location_mismatch": "informational: IP location differs from GPS (common with mobile internet)",
    "network_location_obscured": "informational: VPN, proxy or hosting network",
    "static_location_series": "informational: a still series of coordinates",
    "gps_accuracy_warning": "informational: GPS accuracy is weak",
    "liveness_not_verified": "informational: set on every check-in while the anti-spoofing model is off; not a signal",
    "attendance_during_absence": "checked in during an approved absence (vacation, sick leave)",
}


class ListObjectsParams(ToolParams):
    include_inactive: bool = Field(default=False, description="Also return archived objects.")


class PeriodParams(ToolParams):
    date_from: date = Field(description="First local day, YYYY-MM-DD.")
    date_to: date = Field(description=f"Last local day, YYYY-MM-DD. At most {MAX_RANGE_DAYS} days after date_from.")

    @model_validator(mode="after")
    def _check_range(self) -> PeriodParams:
        if self.date_to < self.date_from:
            raise ValueError("date_to is before date_from")
        if (self.date_to - self.date_from).days > MAX_RANGE_DAYS:
            raise ValueError(f"the range is longer than {MAX_RANGE_DAYS} days")
        return self


class AttendanceEventsParams(PeriodParams):
    object_id: UUID | None = Field(default=None, description="Only this object; leave out for all objects.")
    employee_id: UUID | None = Field(default=None, description="Only this employee; leave out for everyone.")
    suspicious_only: bool = Field(default=False, description="Only events flagged by anti-fraud checks.")
    limit: int = Field(default=100, ge=1, le=MAX_EVENTS, description=f"How many events to return, 1–{MAX_EVENTS}.")


class SharedPhonesParams(PeriodParams):
    pass


class EventDetailParams(ToolParams):
    event_id: UUID = Field(description="Attendance event id from get_attendance_events.")


class PresenceTodayParams(ToolParams):
    status: Literal["all", "in", "out", "absent"] = Field(
        default="all",
        description="all; in — on site now; out — checked out today; absent — no check-in today."
    )


def attendance_tools(nazar: NazarClient, *, page_size: int = PAGE_SIZE) -> tuple[Tool, ...]:
    async def get_shared_phones(params: SharedPhonesParams) -> dict[str, Any]:
        events: list[dict[str, Any]] = []
        while len(events) < MAX_SCANNED_EVENTS:
            page = await call_nazar(
                nazar.get(
                    "/attendance/logs",
                    {
                        "date_from": _local_day_start(params.date_from).isoformat(),
                        "date_to": _local_day_start(params.date_to + timedelta(days=1)).isoformat(),
                        "limit": page_size,
                        "offset": len(events),
                    },
                )
            )
            events.extend(page)
            if len(page) < page_size:
                break
        return {
            "phones": shared_phones(events)[:MAX_SHARED_PHONES],
            "events_scanned": len(events),
            "truncated": len(events) >= MAX_SCANNED_EVENTS,
        }

    async def list_objects(params: ListObjectsParams) -> list[dict[str, Any]]:
        objects = await call_nazar(nazar.get("/objects", {"include_inactive": params.include_inactive}))
        return [_object_view(item) for item in objects]

    async def get_attendance_events(params: AttendanceEventsParams) -> dict[str, Any]:
        events = await call_nazar(
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
        detail = await call_nazar(nazar.get(f"/attendance/events/{params.event_id}"))
        return _event_detail_view(detail)

    async def get_presence_today(params: PresenceTodayParams) -> dict[str, Any]:
        page = await call_nazar(
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
                "GPS accuracy, review status and the device key (the same key on check-ins of "
                "different people means one phone). One call covers the whole company: filter by "
                "object or employee only when the question is about them."
            ),
            params=AttendanceEventsParams,
            handler=get_attendance_events,
        ),
        Tool(
            name="get_shared_phones",
            description=(
                "Phones used by several people in a period, counted by code over every check-in: who, "
                "from when to when, how many check-ins came minutes after another person's on the same "
                "phone, and when each person last checked in from a different phone. Start here when "
                "the question is about who really comes to work."
            ),
            params=SharedPhonesParams,
            handler=get_shared_phones,
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



def _local_day_start(day: date) -> datetime:
    return datetime.combine(day, time.min, tzinfo=COMPANY_TZ)


def _str_or_none(value: UUID | None) -> str | None:
    return str(value) if value is not None else None



def _explain_flags(codes: list[str]) -> list[dict[str, str]]:
    return [
        {"code": code, "meaning": FLAG_MEANINGS.get(code, "no description")}
        for code in dict.fromkeys(codes)  # unique, order kept
    ]


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
        "at": local_time(item.get("received_at_server")),
        "employee_id": item.get("employee_id"),
        "employee_name": item.get("employee_name"),
        "object_id": item.get("object_id"),
        "object_name": item.get("object_name"),
        "face_matched": item.get("matched"),
        "inside_geofence": item.get("inside_geofence"),
        "gps_accuracy_m": item.get("accuracy_meters"),
        "flags": _explain_flags([*item.get("risk_flags", []), *item.get("suspicious_flags", [])]),
        "review_status": item.get("review_status"),
        "has_photo": bool(item.get("photo_url")),
        # The same key on check-ins of different people: one phone (NAZAR change 0003).
        "device_key": item.get("device_key"),
    }


def _event_detail_view(item: dict[str, Any]) -> dict[str, Any]:
    previous = item.get("previous_event")
    return {
        **_event_view(item),
        "risk_score": item.get("risk_score"),
        "risk_level": item.get("risk_level"),
        "distance_to_site_m": item.get("distance_to_object_meters"),
        "captured_at_client": local_time(item.get("captured_at_client")),
        "location_samples": len(item.get("location_samples") or []),
        "network": item.get("network_context") or {},
        "previous_event": None
        if previous is None
        else {
            "id": previous["id"],
            "type": previous["event_type"],
            "object_name": previous.get("object_name"),
            "at": local_time(previous.get("received_at_server")),
            # Between the two check-ins, not to the site: read live as "the
            # previous one was 6.6 km from the shop too", which was false.
            "distance_between_check_ins_m": previous.get("distance_meters"),
        },
        "review_comment": item.get("review_comment"),
        "reviewed_by": item.get("reviewed_by_name"),
    }


def shared_phones(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Phones that several people check in from, most people first.

    Counted by code, not by the model: over every check-in of the period, so
    the analyst sees a pattern that no single page of events shows — who
    switched to someone else's phone, and from when.
    """
    by_phone: dict[str, list[dict[str, Any]]] = {}
    for item in events:
        if item.get("device_key"):
            by_phone.setdefault(item["device_key"], []).append(item)

    phones = []
    for key, items in by_phone.items():
        people = {item["employee_id"] for item in items}
        if len(people) < 2:
            continue
        check_ins = sorted((item for item in items if item["event_type"] == "check_in"), key=_moment)
        bursts = sum(
            1
            for previous, current in zip(check_ins, check_ins[1:])
            if current["employee_id"] != previous["employee_id"]
            and _moment(current) - _moment(previous) <= timedelta(minutes=BURST_MINUTES)
        )
        phones.append({
            "id": key,
            "people_count": len(people),
            "objects": sorted({item.get("object_name") or "" for item in items} - {""}),
            "days": len({_moment(item).astimezone(COMPANY_TZ).date() for item in items}),
            "check_ins_minutes_after_another_person": bursts,
            "people": sorted(
                (_phone_user(key, employee, items, events) for employee in people), key=lambda person: person["first_at"]
            ),
        })
    return sorted(phones, key=lambda phone: (-phone["people_count"], -phone["check_ins_minutes_after_another_person"]))


def _phone_user(key: str, employee: str, items: list[dict[str, Any]], events: list[dict[str, Any]]) -> dict[str, Any]:
    mine = sorted((item for item in items if item["employee_id"] == employee), key=_moment)
    elsewhere = [
        item for item in events if item["employee_id"] == employee and item.get("device_key") not in (None, key)
    ]
    return {
        "employee_id": employee,
        "employee_name": mine[0].get("employee_name"),
        "check_ins": sum(1 for item in mine if item["event_type"] == "check_in"),
        "check_outs": sum(1 for item in mine if item["event_type"] == "check_out"),
        "first_at": local_time(mine[0]["received_at_server"]),
        "last_at": local_time(mine[-1]["received_at_server"]),
        "last_on_own_or_other_phone": local_time(max(elsewhere, key=_moment)["received_at_server"]) if elsewhere else None,
    }


def _moment(item: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(item["received_at_server"])


def _presence_view(page: dict[str, Any]) -> dict[str, Any]:
    return {
        "summary": page.get("summary", {}),
        "objects": page.get("objects", []),
        "employees": [
            {
                "id": entry["employee"]["id"],
                "name": entry["employee"]["full_name"],
                "status": entry.get("status"),
                "object_id": entry.get("object_id"),
                "object_name": entry.get("object_name"),
                "last_event_at": local_time(entry.get("last_event_at")),
            }
            for entry in page.get("items", [])
        ],
        "total": page.get("total"),
    }
