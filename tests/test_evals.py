from typing import Any

from nazar_agent.evals.score import score_analysis, score_import
from nazar_agent.evals.summary import summarize

DEAD = {"e-timur": "Timur Hasanov", "e-gul": "Gulchehra Ibragimova", "e-mohira": "Mohira Yunusova", "e-feruza": "Feruza Karimova"}
OTHERS = {"e-rustam": "Rustam Yuldashev", "e-dilshod": "Dilshod Mirzaev", "e-hurshid": "Hurshid Rahmonov", "e-zuhra": "Zuhra Nabieva", "e-kamola": "Kamola Tashpulatova"}


def _events() -> list[dict[str, Any]]:
    people = {**DEAD, **OTHERS}
    return [
        {"id": f"ev-{employee_id}", "type": "check_in", "employee_id": employee_id, "employee_name": name, "device_key": "phone-rustam"}
        for employee_id, name in people.items()
    ]


def _outcome(*cards: dict[str, Any], clean: bool = True) -> dict[str, Any]:
    steps = [
        {"name": "get_attendance_events", "output": {"events": _events()}},
        {"name": "list_objects", "output": [{"id": "obj-yash", "name": "Yashnobod residential, finishing", "radius_meters": 100.0}]},
        {
            "name": "get_setup_check",
            "output": {"findings": [{"id": "setup:mass_lateness", "employee_ids": ["e-zuhra", "e-kamola"], "employee_names": ["Zuhra Nabieva", "Kamola Tashpulatova"]}]},
        },
    ]
    return {
        "analysis": {"run": {"steps": steps}, "clean_first_time": clean, "rejected": []},
        "recommendations": {"cards": list(cards)},
    }


def _card(confidence: str, facts: list[list[tuple[str, str]]], *, subject: tuple[str, str, str] = ("object", "", ""), actions=()) -> dict[str, Any]:
    kind, subject_id, name = subject
    return {
        "finding": {
            "title": "t",
            "subject_kind": kind,
            "subject_id": subject_id,
            "subject_name": name,
            "facts": [{"evidence": [{"kind": k, "id": i} for k, i in refs]} for refs in facts],
        },
        "confidence": confidence,
        "skeptic": {"verdict": "concern_supported"},
        "actions": list(actions),
        "rejected_actions": [],
    }


def test_dead_souls_found_through_their_check_ins():
    card = _card("high", [[("event", f"ev-{employee_id}") for employee_id in DEAD]])

    result = score_analysis(_outcome(card), "dead_souls")

    assert sorted(result["found"]) == sorted(DEAD.values())
    assert result["missed"] == []
    assert result["passed"] is True


def test_low_confidence_card_finds_nobody():
    card = _card("low", [[("event", f"ev-{employee_id}") for employee_id in DEAD]])

    result = score_analysis(_outcome(card), "dead_souls")

    assert result["found"] == []
    assert result["passed"] is False


def test_contrast_with_the_honest_foreman_is_not_an_accusation():
    card = _card(
        "high",
        [[("event", f"ev-{employee_id}")] for employee_id in DEAD] + [[("event", "ev-e-dilshod")]],
    )

    result = score_analysis(_outcome(card), "dead_souls")

    assert result["falsely_accused"] == []


def test_separate_card_about_the_honest_foreman_is_an_accusation():
    souls = _card("high", [[("event", f"ev-{employee_id}") for employee_id in DEAD]])
    honest = _card("medium", [[("event", "ev-e-dilshod")], [("event", "ev-e-hurshid")]])

    result = score_analysis(_outcome(souls, honest), "dead_souls")

    assert result["falsely_accused"] == ["Dilshod Mirzaev", "Hurshid Rahmonov"]
    assert result["passed"] is False


def test_low_confidence_mention_is_allowed_but_a_pay_cut_is_not():
    mention = _card("low", [[("event", "ev-e-hurshid")]])
    cut = _card("low", [[("event", "ev-e-dilshod")]], actions=[{"name": "void_shift", "params": {"event_id": "ev-e-dilshod"}}])

    result = score_analysis(_outcome(mention, cut), "dead_souls")

    assert result["falsely_accused"] == ["Dilshod Mirzaev"]


def test_confirming_a_dead_soul_is_not_a_false_accusation():
    card = _card(
        "high",
        [[("event", f"ev-{employee_id}") for employee_id in DEAD]],
        actions=[{"name": "review_event", "params": {"event_id": "ev-e-timur", "status": "confirmed"}}],
    )

    assert score_analysis(_outcome(card), "dead_souls")["falsely_accused"] == []


def test_schedule_card_needs_the_fix_to_pass():
    card = _card("medium", [[("setup_finding", "setup:mass_lateness")]], subject=("object", "obj-yash", "Yashnobod residential, finishing"))
    fixed = _card(
        "medium",
        [[("setup_finding", "setup:mass_lateness")]],
        subject=("object", "obj-yash", "Yashnobod residential, finishing"),
        actions=[{"name": "change_schedule", "params": {"object_id": "obj-yash", "shift_start": "09:00"}}],
    )
    wrong_hours = _card(
        "medium",
        [[("setup_finding", "setup:mass_lateness")]],
        subject=("object", "obj-yash", "Yashnobod residential, finishing"),
        actions=[{"name": "change_schedule", "params": {"object_id": "obj-yash", "shift_start": "10:00"}}],
    )

    without_fix = score_analysis(_outcome(card), "wrong_schedule")
    with_fix = score_analysis(_outcome(fixed), "wrong_schedule")

    assert without_fix["schedule_found"] is True
    assert without_fix["falsely_accused"] == []
    assert without_fix["passed"] is False
    assert with_fix["passed"] is True
    assert score_analysis(_outcome(wrong_hours), "wrong_schedule")["schedule_fix_proposed"] is False


def test_lateness_card_about_the_crew_is_an_accusation():
    card = _card("medium", [[("event", "ev-e-zuhra")], [("event", "ev-e-kamola")]])

    result = score_analysis(_outcome(card), "wrong_schedule")

    assert result["schedule_found"] is False
    assert result["falsely_accused"] == ["Kamola Tashpulatova", "Zuhra Nabieva"]


def test_import_flags_and_false_flags():
    rows = [
        {"full_name": "Норматов Азамат", "issues": ["похож на действующего сотрудника «Azamat Normatov» — может быть дубль"]},
        {"full_name": "Юсупов Сардор", "issues": []},
        {"full_name": "Юсупов Сардор", "issues": ["встречается в списке дважды"]},
        {"full_name": "Эргашев", "issues": []},
        {"full_name": "Абдуллаев Ботир", "issues": ["объект «Сергели» не найден — выберите из списка"]},
    ]
    expected = {"Azamat Normatov": "duplicate", "Сардор Юсупов": "twice", "Эргашев": "surname"}

    result = score_import(rows, expected)

    assert result["caught"] == ["Azamat Normatov: duplicate", "Сардор Юсупов: twice"]
    assert result["missed"] == ["Эргашев: surname"]
    assert len(result["false_flags"]) == 1
    assert result["passed"] is False


def test_summary_counts_runs_and_errors():
    results = [
        {"case": "dead_souls", "run": 1, "passed": True, "found": list(DEAD.values()), "falsely_accused": [], "clean_first_time": True, "actions_valid": 2, "actions_rejected": 0, "cost_usd": 0.02},
        {"case": "dead_souls", "run": 2, "passed": False, "error": "TimeoutError: "},
    ]

    table = summarize(results)

    assert "| 4 «dead souls» checked in from the foreman's phone | 2 | 1/2 | 4/4 people | 0 in 1 runs | 1/1 | 2/2 | $0.020 |" in table
    assert "dead_souls #2 failed" in table


def test_a_card_about_setup_accuses_no_one():
    card = _card("medium", [[("event", "ev-e-dilshod"), ("event", "ev-e-hurshid")]], subject=("company", "", "Вся компания"))
    card["finding"]["concern"] = "setup"

    assert score_analysis(_outcome(card), "dead_souls")["falsely_accused"] == []


def test_asking_about_a_real_absence_is_not_an_accusation():
    outcome = _outcome(
        _card("medium", [[("timesheet_day", "e-dilshod:2026-10-27")]], subject=("employee", "e-dilshod", "Dilshod Mirzaev"))
    )
    outcome["analysis"]["run"]["steps"].append(
        {"name": "get_timesheet_summary", "output": {"unusual_days": [{"id": "e-dilshod:2026-10-27", "day_kind": "absent"}]}}
    )

    assert score_analysis(outcome, "dead_souls")["falsely_accused"] == []


def test_a_card_about_a_person_is_not_exempt_by_its_label():
    card = _card("medium", [[("event", "ev-e-dilshod")]], subject=("employee", "e-dilshod", "Dilshod Mirzaev"))
    card["finding"]["concern"] = "setup"

    assert score_analysis(_outcome(card), "dead_souls")["falsely_accused"] == ["Dilshod Mirzaev"]
