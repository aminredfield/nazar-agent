"""The demo world as NAZAR's JSON: the same fields of the same types (tests/contracts/).

Fields the demo has no notion of (departments, pay rates, night rules,
disputes) are answered empty, as NAZAR answers a company that does not use them.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from nazar_agent.demo.timesheet import (
    AUTO_CLOSE_AFTER_MINUTES,
    EARLY_LEAVE_GRACE_MINUTES,
    MAX_OVERTIME_MINUTES,
    Adjustment,
    Book,
    assignment_id,
    day_row,
    iso,
    local_day,
    plan_of,
)
from nazar_agent.demo.world import (
    COMPANY_NAME,
    LATE_GRACE_MINUTES,
    OWNER_NAME,
    TZ,
    Event,
    Person,
    Site,
    World,
    distance_meters,
    stable_id,
)

THRESHOLD = 0.5  # face similarity NAZAR accepts
MAX_SHIFT_HOURS = 12
# Flags that only inform; the rest make a check-in suspicious and raise its risk.
INFORMATIONAL_FLAGS = frozenset({"gps_accuracy_warning", "network_location_mismatch", "liveness_not_verified"})
RISK_PER_FLAG = 35
OWNER_ID = stable_id("user", OWNER_NAME)
ACCOUNT_ID = stable_id("account", OWNER_NAME)
PERMISSIONS = ["attendance.view", "attendance.review", "employees.manage", "objects.manage", "timesheet.manage"]


def company(world: World) -> dict[str, Any]:
    created = iso(datetime.combine(world.first_day, datetime.min.time()).replace(tzinfo=world.now.tzinfo))
    return {
        "id": world.company_id, "name": COMPANY_NAME, "is_active": True, "is_demo": True,
        "demo_started_at": created, "demo_expires_at": None, "blocked_at": None, "blocked_reason": None,
        "subscription_plan": "business", "subscription_status": "active", "billing_period": "monthly",
        "paid_until": iso(world.now + timedelta(days=60)), "contact_phone": "+998 90 000 00 00", "created_at": created,
    }


def sign_in(world: World, email: str) -> dict[str, Any]:
    """What /auth/login and /auth/refresh answer: the person, the company and its features."""
    return {
        "company": company(world),
        "user": {
            "id": OWNER_ID, "account_id": ACCOUNT_ID, "company_id": world.company_id, "full_name": OWNER_NAME,
            "email": email, "is_active": True, "suspended_reason": None, "email_verified_at": company(world)["created_at"],
            "has_google_auth": False, "is_primary": True, "preferred_language": "ru", "role": "owner",
            "permissions": PERMISSIONS, "created_at": company(world)["created_at"],
        },
        "companies": [company(world)],
        "features": {
            "experimental_mobile": False, "camera_integrations": False, "payroll": True, "night_rules": False,
            "employee_portal": False, "plan_fact": True, "disputes": False, "fraud_analytics": True,
            "weekly_report": True, "route_tracking": False, "extra_roles": False, "ai_agent": True,
            "plan_code": "business", "plan_tier": "business", "is_legacy_plan": False, "admin_limit": None,
            "show_payment_page_during_demo": False, "support_url": "", "show_beta_badge": True,
        },
    }


def session(world: World, token: str, expires_at: datetime, email: str) -> dict[str, Any]:
    return {
        "access_token": token, "expires_at": iso(expires_at), "company_id": world.company_id,
        "company_name": COMPANY_NAME, "full_name": OWNER_NAME, "email": email, "role": "owner",
    }


def site(world: World, item: Site, updated_at: datetime) -> dict[str, Any]:
    schedule = item.schedule_on(world.today)
    return {
        "id": item.id, "company_id": world.company_id, "name": item.name, "address": item.address,
        "latitude": item.latitude, "longitude": item.longitude, "radius_meters": item.radius_meters,
        "is_active": item.is_active, "shift_start": schedule.shift_start, "shift_end": schedule.shift_end,
        "max_shift_hours": MAX_SHIFT_HOURS, "late_grace_minutes": LATE_GRACE_MINUTES,
        "early_leave_grace_minutes": EARLY_LEAVE_GRACE_MINUTES, "overtime_after_minutes": 0,
        "max_overtime_minutes": MAX_OVERTIME_MINUTES, "auto_close_after_minutes": AUTO_CLOSE_AFTER_MINUTES,
        "work_schedule_mode": schedule.mode, "working_days": list(schedule.working_days), "trusted_networks": [],
        "assigned_employee_count": sum(1 for person in world.people.values() if person.site_id == item.id and person.is_active),
        "schedule_conflict_count": 0, "created_at": iso(item.created_at), "updated_at": iso(updated_at),
    }


def assignments(world: World, site_id: str) -> dict[str, Any]:
    people = sorted((person for person in world.people.values() if person.site_id == site_id), key=lambda person: person.code)
    return {
        "object_id": site_id,
        "assignments": [
            {
                "employee_id": person.id, "employee_name": person.name, "employee_external_id": person.code,
                "is_active": person.is_active, "assigned_at": iso(person.created_at), "schedule_mode": "object_default",
                "shift_start": None, "shift_end": None, "working_days": None, "assignment_id": assignment_id(person),
                "starts_on": person.assigned_from.isoformat(), "ends_on": None, "schedule_type": None,
                "cycle_anchor_date": None, "cycle_pattern": None, "is_24_hours": False, "shift_duration_minutes": 0,
                "origin": "manual", "source_department_name": None, "schedule_conflict": False,
                "conflicting_department_ids": [], "conflicting_department_names": [], "primary_department_id": None,
                "effective_schedule_source": None, "effective_shift_start": None, "effective_shift_end": None,
            }
            for person in people
        ],
    }


def employee(world: World, person: Person) -> dict[str, Any]:
    events = world.events_of(person.id)
    last = events[-1] if events else None
    home = world.sites.get(person.site_id or "")
    return {
        "id": person.id, "full_name": person.name, "employee_id": person.code,
        "preview_image_url": f"/media/persons/{person.id}.jpg", "is_active": person.is_active, "departments": [],
        "created_at": iso(person.created_at), "updated_at": iso(person.created_at), "face_ready": True,
        "has_pay_rate": False, "primary_object_name": home.name if home else None, "object_count": 1 if home else 0,
        "schedule": None, "last_event_type": last.event_type if last else None, "last_event_at": iso(last.at) if last else None,
    }


def photo_url(event: Event) -> str:
    return f"/media/attendance/{event.id}.jpg"


def suspicious(event: Event) -> list[str]:
    return [flag for flag in event.risk_flags if flag not in INFORMATIONAL_FLAGS]


def event_item(world: World, book: Book, event: Event) -> dict[str, Any]:
    person = world.people[event.employee_id]
    return {
        "id": event.id, "event_type": event.event_type, "matched": event.similarity >= THRESHOLD,
        "similarity": event.similarity, "threshold": THRESHOLD, "inside_geofence": event.inside,
        "employee_id": person.id, "employee_name": person.name, "employee_external_id": person.code,
        "object_id": event.object_id, "object_name": world.sites[event.object_id].name,
        "latitude": event.latitude, "longitude": event.longitude, "accuracy_meters": event.accuracy,
        "request_ip": event.request_ip, "risk_flags": list(event.risk_flags),
        "schedule_snapshot": schedule_snapshot(world, book, event), "suspicious_flags": suspicious(event),
        "review_status": event.review_status, "photo_url": photo_url(event), "received_at_server": iso(event.at),
        "device_key": event.device_key,
    }


def event_detail(world: World, book: Book, event: Event) -> dict[str, Any]:
    site_ = world.sites[event.object_id]
    mine = world.events_of(event.employee_id)
    position = mine.index(event)
    previous = mine[position - 1] if position else None
    risk = min(100, RISK_PER_FLAG * len(suspicious(event)))
    return {
        **event_item(world, book, event),
        "distance_to_object_meters": round(distance_meters(event.latitude, event.longitude, site_.latitude, site_.longitude), 1),
        "captured_at_client": iso(event.at - timedelta(seconds=1)),
        "location_samples": [
            {
                "latitude": event.latitude, "longitude": event.longitude, "accuracy_meters": event.accuracy,
                "captured_at_client": iso(event.at - timedelta(seconds=seconds)), "provider": "gps", "speed_mps": None,
            }
            for seconds in (6, 4, 2)
        ],
        "network_context": {},
        "risk_score": risk,
        "risk_level": "high" if risk >= 70 else "medium" if risk >= 30 else "low",
        "previous_event": None if previous is None else {
            "id": previous.id, "event_type": previous.event_type, "object_name": world.sites[previous.object_id].name,
            "latitude": previous.latitude, "longitude": previous.longitude, "received_at_server": iso(previous.at),
            "distance_meters": distance_meters(previous.latitude, previous.longitude, event.latitude, event.longitude),
        },
        "admin_notified_at": iso(event.at) if risk else None,
        "review_comment": event.review_comment,
        "reviewed_at": iso(event.reviewed_at),
        "reviewed_by_name": event.reviewed_by,
    }


def schedule_snapshot(world: World, book: Book, event: Event) -> dict[str, Any]:
    """The plan of the event's day and its session counted alone, as stored with a check-in."""
    person = world.people[event.employee_id]
    day = local_day(event.at)
    plan = plan_of(world, person, day)
    schedule = world.sites[event.object_id].schedule_on(day)
    check_in = next((item for item in world.events_of(person.id) if item.id == event.check_in_id), event)
    check_out = event if event.event_type == "check_out" else None
    counted = [(check_in.at, check_out.at)] if check_out else []
    row = day_row(world, Book(), person, day, override=counted)
    if not counted and plan is not None:
        late_by = int((check_in.at - plan[0]).total_seconds() // 60)
        row["late_minutes"] = late_by if late_by > LATE_GRACE_MINUTES else 0
    overtime = row["overtime_minutes"] + row["unpaid_minutes"]
    return {
        "source": "object_default",
        "metrics": {
            "is_late": row["late_minutes"] > 0, "has_overtime": overtime > 0, "late_minutes": row["late_minutes"],
            "actual_minutes": row["worked_minutes"], "is_early_leave": row["early_leave_minutes"] > 0,
            "counted_minutes": row["worked_minutes"], "regular_minutes": row["regular_minutes"],
            "requires_review": row["needs_review"], "overtime_minutes": overtime,
            "overtime_exceeded": row["unpaid_minutes"] > 0, "early_leave_minutes": row["early_leave_minutes"],
            "overtime_excess_minutes": row["unpaid_minutes"],
        },
        "shift_end": schedule.shift_end, "work_date": day.isoformat(), "night_rule": "unreviewed", "is_24_hours": False,
        "shift_start": schedule.shift_start, "exception_id": None, "is_scheduled": plan is not None,
        "working_days": list(schedule.working_days), "assignment_id": assignment_id(person, event.object_id),
        "auto_close_at": iso(plan[1] + timedelta(minutes=AUTO_CLOSE_AFTER_MINUTES)) if plan else None,
        "cycle_pattern": None, "schedule_type": "weekly", "planned_end_at": iso(plan[1]) if plan else None,
        "planned_minutes": row["schedule_norm_minutes"], "schedule_source": schedule.mode,
        "planned_start_at": iso(plan[0]) if plan else None, "cycle_anchor_date": None,
        "late_grace_minutes": LATE_GRACE_MINUTES, "max_overtime_minutes": MAX_OVERTIME_MINUTES,
        "night_reduction_side": None, "night_nominal_minutes": None, "legacy_max_shift_hours": MAX_SHIFT_HOURS,
        "overtime_after_minutes": 0, "schedule_effective_from": schedule.effective_from.isoformat(),
        "auto_close_after_minutes": AUTO_CLOSE_AFTER_MINUTES, "early_leave_grace_minutes": EARLY_LEAVE_GRACE_MINUTES,
    }


def presence(world: World, status: str, limit: int, offset: int) -> dict[str, Any]:
    """Who is on site, has left or has not come, by each person's latest event.

    As NAZAR does: the latest event of the last two days decides — "in" while
    the shift is open, "out" after a check-out; the counters next to it are
    about today and the schedule.
    """
    today, now = world.today, world.now
    since = datetime.combine(today - timedelta(days=2), datetime.min.time(), tzinfo=TZ)
    day_start = datetime.combine(today, datetime.min.time(), tzinfo=TZ)
    chart_days = [today - timedelta(days=29 - n) for n in range(30)]
    people = sorted((person for person in world.people.values() if person.is_active), key=lambda person: person.name)

    rows = []
    for person in people:
        recent = [event for event in world.events_of(person.id) if event.at >= since]
        last = recent[-1] if recent else None
        if last is None:
            state = "absent"
        elif last.event_type == "check_in":
            plan = plan_of(world, person, local_day(last.at))
            closes = (plan[1] if plan else last.at + timedelta(hours=MAX_SHIFT_HOURS)) + timedelta(minutes=AUTO_CLOSE_AFTER_MINUTES)
            state = "in" if now < closes else "absent"
        else:
            state = "out"
        rows.append((person, last, state))

    on_site = [row for row in rows if row[2] == "in"]
    checked_today = sum(1 for _, last, _ in rows if last is not None and last.at >= day_start)
    planned = [(person, plan_of(world, person, today)) for person in people]
    planned = [(person, plan) for person, plan in planned if plan is not None]
    started = [person for person, plan in planned if now >= plan[0] + timedelta(minutes=LATE_GRACE_MINUTES)]
    arrived = {
        event.employee_id for event in world.events
        if event.event_type == "check_in" and event.at >= day_start
    }
    shown = [row for row in rows if status == "all" or row[2] == status]
    by_site: dict[str, int] = {}
    for _, last, _ in on_site:
        by_site[last.object_id] = by_site.get(last.object_id, 0) + 1
    return {
        "items": [
            {
                "employee": employee(world, person), "status": state,
                "object_id": last.object_id if last else None,
                "object_name": world.sites[last.object_id].name if last else None,
                "last_event_at": iso(last.at) if last else None,
                "chart": [
                    any(event.event_type == "check_in" and local_day(event.at) == day for event in world.events_of(person.id))
                    for day in chart_days
                ],
            }
            for person, last, state in shown[offset:offset + limit]
        ],
        "total": len(shown),
        "limit": limit,
        "offset": offset,
        "summary": {
            "total_employees": len(people),
            "on_site": len(on_site),
            "left_today": sum(1 for _, last, state in rows if state == "out" and last.at >= day_start),
            "absent": max(len(people) - checked_today, 0),
            "attendance_rate": round(checked_today / len(people) * 100) if people else 0,
            "scheduled_today": len(planned),
            "expected_now": sum(1 for _, plan in planned if plan[0] <= now < plan[1]),
            "not_arrived": sum(1 for person in started if person.id not in arrived),
            "day_off": len(people) - len(planned),
            "shift_attendance_rate": round(sum(1 for person in started if person.id in arrived) / len(started) * 100)
            if started else None,
        },
        "objects": [
            {
                "object_id": site_id, "object_name": world.sites[site_id].name,
                "object_address": world.sites[site_id].address, "employees_count": count,
            }
            for site_id, count in sorted(by_site.items(), key=lambda item: -item[1])
        ],
    }


def adjustment(item: Adjustment, site_id: str | None) -> dict[str, Any]:
    return {
        "id": item.id, "employee_id": item.employee_id, "work_date": item.work_date.isoformat(),
        "target_key": f"assignment:{item.assignment_id}", "assignment_id": item.assignment_id, "object_id": site_id,
        "field": "work_intervals",
        "new_value": [{"end_at": end.isoformat(), "start_at": start.isoformat()} for start, end in item.intervals],
        "reason": item.reason, "is_active": item.is_active, "created_by_admin_id": item.created_by,
        "created_at": iso(item.created_at),
    }


def day_detail(world: World, book: Book, person: Person, day: date) -> dict[str, Any]:
    row = day_row(world, book, person, day)
    events = [event for event in world.events_of(person.id) if local_day(event.at) == day]
    active = [item for item in book.adjustments if item.is_active and item.employee_id == person.id and item.work_date == day]
    return {
        "day": row, "is_closed": False, "period_status": None, "period_id": None, "closed_at": None,
        "closed_by_name": None, "document_version": None,
        "events": [
            {
                "id": event.id, "event_type": event.event_type, "received_at_server": iso(event.at),
                "object_id": event.object_id, "photo_url": photo_url(event), "matched": event.similarity >= THRESHOLD,
                "risk_flags": list(event.risk_flags), "review_status": event.review_status,
            }
            for event in events
        ],
        "adjustments": [adjustment(item, row["object_id"]) for item in active],
        "applied": {
            "calendar_kind": None, "calendar_title": None, "calendar_is_company_override": False, "absence_type": None,
            "absence_counts_as_worked": None, "absence_is_paid": None, "is_excused": False, "excuse_reason": None,
            "adjustment_count": len(active),
        },
        "dispute": None,
    }
