"""The question bank's checks check what they claim, against the demo's truth — before any model is paid."""

from collections import Counter

import pytest

from nazar_agent.demo.world import day_world
from nazar_agent.evals import checks as c
from nazar_agent.evals.cases import WRONG_SCHEDULE_CREW
from nazar_agent.evals.checks import Run, language_of, mentioned
from nazar_agent.evals.questions import BANK, BANK_NOW, INTENTS
from nazar_agent.evals.truth import Truth


@pytest.fixture(scope="module")
def truth():
    return Truth(day_world(BANK_NOW))


def test_the_bank_covers_every_kind_of_question_in_every_language():
    kinds = Counter(question.intent for question in BANK)

    assert len(BANK) >= 70 and set(kinds) == set(INTENTS) and min(kinds.values()) >= 3
    assert {question.lang for question in BANK} == {"ru", "uz", "uz-cyrl", "en"}


def test_the_truth_at_the_bank_moment(truth):
    assert truth.absent(truth.today) == []  # everyone came on Wednesday
    assert truth.late(truth.today) == sorted(WRONG_SCHEDULE_CREW)
    assert truth.absent(truth.yesterday) == ["Ilhom Turaev"]
    assert len(truth.on_site_now()) == 16
    assert truth.worked_minutes("Rustam Yuldashev", *truth.last_week()) == 3700
    assert truth.last(6).isoformat() == "2026-10-24" and truth.last(1).isoformat() == "2026-10-26"


@pytest.mark.parametrize(("name", "text"), [
    ("Zuhra Nabieva", "У Зухры Набиевой опоздание"),
    ("Zuhra Nabieva", "Zukhra Nabieva was late"),
    ("Ilhom Turaev", "Ilhom To'raev kelmadi"),
    ("Timur Hasanov", "Тимура"),
])
def test_a_name_is_found_in_any_case_and_script(name, text):
    assert mentioned(name, text)


def test_a_name_is_not_found_where_it_is_not():
    assert not mentioned("Timur Hasanov", "Rustam Yuldashev и Azamat Normatov")


@pytest.mark.parametrize("text", ["3700 минут", "61 ч 40 мин", "61,7 часа", "около 62 часов"])
def test_a_duration_is_found_in_minutes_or_hours(text, truth):
    check = c.minutes(lambda t: 3700, "hours")

    assert check(Run(summary=text, text=text), truth) is None


def test_a_wrong_duration_fails(truth):
    assert c.minutes(lambda t: 3700, "hours")(Run(summary="50 часов", text="50 часов"), truth) is not None


@pytest.mark.parametrize(("text", "lang"), [
    ("Сегодня все пришли на работу, опозданий нет.", "ru"),
    ("Bugun hamma ishga keldi, kechikkanlar yo'q va hamma joyida.", "uz"),
    ("Ўтган ҳафта энг кўп Рустам ишлади, бу жуда яхши натижа.", "uz-cyrl"),
    ("Everyone is on site and nobody was late today.", "en"),
])
def test_the_language_of_an_answer(text, lang):
    assert language_of(text) == lang


def test_every_check_runs_on_an_answer_and_on_a_failure(truth):
    answer = Run(summary="Все на месте, ничего проверять не нужно.", text="Все на месте, ничего проверять не нужно.")
    failure = Run(error="APITimeoutError: Request timed out.")

    for question in BANK:
        for check in question.checks:
            check(answer, truth)
            assert check(failure, truth) is not None, (question.id, check.label)


def test_an_off_topic_answer_that_investigates_fails(truth):
    said = "Погоду не подскажу, но могу проверить отметки."
    investigated = Run(summary=said, text=said, tools=[("get_presence_today", {})])
    off_topic = next(question for question in BANK if question.id == "I01")

    failed = [check.label for check in off_topic.checks if check(investigated, truth)]

    assert failed == ["no NAZAR reads"]
