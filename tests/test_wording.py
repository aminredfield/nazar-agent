"""The owner's sentence for each action, written by code from its parameters."""

from nazar_agent.findings import Evidence
from nazar_agent.recommendations.wording import about, describe, duration

SITE = "5a640598-9da0-4b0b-b162-1491175030cc"
EMPLOYEE = "075e27b5-d1b1-4d3c-9925-149e7367a2c7"
EVENT = "d5844f96-47c5-4dad-ae05-314d83a8f63f"
SCHEDULE_PREVIEW = {
    "before": {"shift_start": "08:00", "shift_end": "18:00", "working_days": [1, 2, 3, 4, 5, 6]},
    "after": {"shift_start": "09:00", "shift_end": "19:00", "working_days": [1, 2, 3, 4, 5, 6]},
}
DAY_PREVIEW = {"before": {"paid_minutes": 480}, "after": {"paid_minutes": 545}}


def card(**named):
    return {"subject": {"kind": "object", "id": SITE, "name": "Yashnobod residential, finishing"}, "about": named}


def test_schedule_names_the_site_and_both_shifts():
    params = {"object_id": SITE, "shift_start": "09:00", "shift_end": "19:00", "working_days": None, "reason": "…"}

    text = describe("change_schedule", params, SCHEDULE_PREVIEW, card(site="Yashnobod residential, finishing"), "ru")

    assert text == (
        "Объект Yashnobod residential, finishing: смена 09:00–19:00 вместо 08:00–18:00 с сегодняшнего дня. "
        "Прошлые дни NAZAR не пересчитает."
    )


def test_schedule_says_changed_working_days_in_english():
    params = {"object_id": SITE, "shift_start": "09:00", "shift_end": "19:00", "working_days": [1, 2, 3, 4, 5], "reason": "…"}
    preview = {"before": SCHEDULE_PREVIEW["before"], "after": {**SCHEDULE_PREVIEW["after"], "working_days": [1, 2, 3, 4, 5]}}

    text = describe("change_schedule", params, preview, card(site="Yashnabad"), "en")

    assert "shift 09:00–19:00 instead of 08:00–18:00" in text
    assert "working days Mon, Tue, Wed, Thu, Fri instead of Mon, Tue, Wed, Thu, Fri, Sat" in text


def test_site_name_falls_back_to_the_cards_subject():
    params = {"object_id": SITE, "shift_start": "09:00", "shift_end": "19:00", "working_days": None, "reason": "…"}

    text = describe("change_schedule", params, SCHEDULE_PREVIEW, card(), "uz")

    assert text.startswith("Obyekt Yashnobod residential, finishing: bugundan boshlab smena 09:00–19:00 (08:00–18:00 o'rniga)")


def test_adjusted_day_names_the_person_the_hours_and_pay_before_and_after():
    params = {
        "employee_id": EMPLOYEE, "work_date": "2026-10-23", "reason": "…",
        "intervals": [{"start_at": "2026-10-23T08:05:00+05:00", "end_at": "2026-10-23T18:00:00+05:00"}],
    }

    text = describe("adjust_shift", params, DAY_PREVIEW, card(name="Dilfuza Ergasheva"), "ru")

    assert text == "Dilfuza Ergasheva, 23.10: засчитаю 08:05–18:00. К оплате: 8 ч → 9 ч 5 мин."


def test_voided_day():
    params = {"employee_id": EMPLOYEE, "work_date": "2026-10-23", "reason": "…"}
    preview = {"before": {"paid_minutes": 480}, "after": {"paid_minutes": 0}}

    assert describe("void_shift", params, preview, card(name="Dilfuza Ergasheva"), "uz-cyrl") == (
        "Dilfuza Ergasheva, 23.10: kun hisoblanmaydi. To'lovga: 8 soat → 0 daqiqa."
    )


def test_review_names_the_check_in():
    params = {"event_id": EVENT, "status": "no_violation", "comment": "…"}
    named = card(name="Dilfuza Ergasheva", site="Yashnobod residential, finishing", at="2026-10-27T08:56:42+05:00", kind="check_in")

    assert describe("review_event", params, None, named, "ru") == (
        "Dilfuza Ergasheva, приход 27.10 08:56, Yashnobod residential, finishing: запишу итог разбора «нарушения нет». "
        "Оплата не меняется."
    )


def test_names_are_found_in_what_the_tools_returned():
    evidence = Evidence(
        records={
            EVENT: {"id": EVENT, "type": "check_out", "at": "2026-10-27T19:06:05+05:00", "employee_id": EMPLOYEE,
                    "employee_name": "Dilfuza Ergasheva", "object_id": SITE, "object_name": "Яшнабад"},
        },
        seen_ids=frozenset({EVENT, EMPLOYEE, SITE}),
    )

    assert about("review_event", EVENT, evidence)["kind"] == "check_out"
    assert about("adjust_shift", EMPLOYEE, evidence) == {"name": "Dilfuza Ergasheva"}
    assert about("change_schedule", SITE, evidence) == {"site": "Яшнабад"}
    assert about("change_schedule", "unknown", evidence) == {}


def test_duration():
    assert duration(480) == "8 ч"
    assert duration(545, "en") == "9 h 5 min"
    assert duration(40, "uz") == "40 daqiqa"
