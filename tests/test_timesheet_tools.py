from nazar_agent.tools.timesheet import day_detail_view, setup_check_view, summarize


def row(employee_id, work_date, day_code="work", **values):
    return {
        "employee_id": employee_id,
        "work_date": work_date,
        "object_id": "o1",
        "day_code": day_code,
        "schedule_norm_minutes": 540 if day_code != "day_off" else 0,
        "worked_minutes": 0,
        "paid_minutes": 0,
        "late_minutes": 0,
        "early_leave_minutes": 0,
        "overtime_minutes": 0,
        "is_adjusted": False,
        "needs_review": False,
        "has_open_session": False,
        "has_schedule_overlap": False,
        **values,
    }


MATRIX = {
    "date_from": "2026-10-20",
    "date_to": "2026-10-22",
    "is_closed": False,
    "days": [
        row("e1", "2026-10-20", worked_minutes=513, paid_minutes=513, late_minutes=21, early_leave_minutes=4),
        row("e1", "2026-10-21", worked_minutes=540, paid_minutes=540),
        row("e1", "2026-10-22", "absent"),
        row("e2", "2026-10-20", "day_off"),
    ],
}
NAMES = {"e1": "Азиз Каримов", "e2": "Дилноза Юсупова"}


def test_totals_are_sums_of_nazar_numbers_not_a_recount():
    summary = summarize(MATRIX, NAMES, {"o1": "Магазин Чиланзар"})

    aziz = next(item for item in summary["employees"] if item["id"] == "e1")
    assert aziz["name"] == "Азиз Каримов"
    assert aziz["days_by_kind"] == {"work": 2, "absent": 1}
    assert aziz["worked_minutes"] == 1053
    assert aziz["planned_minutes"] == 1620
    assert (aziz["late_days"], aziz["late_minutes"], aziz["early_leave_minutes"]) == (1, 21, 4)


def test_only_unusual_days_are_listed_with_citable_ids():
    summary = summarize(MATRIX, NAMES, {"o1": "Магазин Чиланзар"})

    days = summary["unusual_days"]
    assert [day["id"] for day in days] == ["e1:2026-10-20", "e1:2026-10-22"]
    assert days[0]["object_name"] == "Магазин Чиланзар"
    assert days[1]["day_kind"] == "absent"
    assert summary["unusual_days_truncated"] is False


def test_day_detail_keeps_the_same_day_id():
    detail = {
        "day": row("e1", "2026-10-22", "absent"),
        "is_closed": False,
        "period_status": None,
        "events": [],
        "adjustments": [],
        "applied": {"absence_type": None, "excuse_reason": None, "calendar_title": None},
        "dispute": None,
    }

    view = day_detail_view(detail)

    assert view["id"] == "e1:2026-10-22"
    assert view["has_dispute"] is False


def test_setup_findings_get_ids_and_meanings():
    view = setup_check_view({
        "date_from": "2026-09-29",
        "date_to": "2026-10-26",
        "findings": [{"code": "mass_lateness", "count": 9, "employee_ids": ["e1"], "employee_names": ["Азиз"], "details": {}}],
    })

    [finding] = view["findings"]
    assert finding["id"] == "setup:mass_lateness"
    assert "shift start" in finding["meaning"]
