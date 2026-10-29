from datetime import date

import pytest

from nazar_agent.assistant import Assistant
from nazar_agent.limits import LimitReached, Limits
from tests.test_sessions import PASS, client_for


def test_questions_per_session_and_a_shared_daily_budget(tmp_path):
    day = [date(2026, 11, 2)]
    limits = Limits(tmp_path / "usage.sqlite3", questions_per_session=2, budget_usd_per_day=0.1, today=lambda: day[0])

    for _ in range(2):
        limits.add_cost("judge-1", limits.reserve("judge-1"), 0.03)
    with pytest.raises(LimitReached, match="2 вопросов на один вход"):
        limits.reserve("judge-1")
    asked = limits.reserve("judge-2")  # another session still may ask
    limits.add_cost("judge-2", asked, 0.05)
    with pytest.raises(LimitReached, match="Дневной лимит демо"):
        limits.reserve("judge-3")  # 0.11 spent by everyone together

    day[0] = date(2026, 11, 3)
    limits.reserve("judge-1")  # a new day


def test_a_cost_lands_on_the_day_the_question_was_asked(tmp_path):
    day = [date(2026, 11, 2)]
    limits = Limits(tmp_path / "usage.sqlite3", questions_per_session=0, budget_usd_per_day=0.1, today=lambda: day[0])

    asked = limits.reserve("judge")
    day[0] = date(2026, 11, 3)  # the answer came after midnight
    limits.add_cost("judge", asked, 0.5)

    limits.reserve("judge")  # the new day starts clean


def test_no_limits_by_default(tmp_path):
    limits = Limits(tmp_path / "usage.sqlite3", questions_per_session=0, budget_usd_per_day=0)
    for _ in range(5):
        limits.add_cost("x", limits.reserve("x"), 10)

    limits.reserve("x")


def test_the_window_stops_at_the_limit(tmp_path, monkeypatch):
    async def read_list(self, **kwargs):
        return [], []

    monkeypatch.setattr(Assistant, "read_list", read_list)
    client, _ = client_for(tmp_path, demo_questions_per_session=1)
    with client:
        client.post("/api/session", json={"handoff": PASS})

        first = client.post("/api/import/read", data={"text": "Иванов"})
        second = client.post("/api/import/read", data={"text": "Петров"})

    assert first.status_code == 200, first.text
    assert second.status_code == 429 and "1 вопросов на один вход" in second.json()["detail"]


def test_a_voice_message_is_checked_against_the_limits_but_not_counted(tmp_path):
    limits = Limits(tmp_path / "usage.sqlite3", questions_per_session=1, budget_usd_per_day=0, today=lambda: date(2026, 11, 2))

    limits.reserve("judge-1", question=False)
    limits.reserve("judge-1", question=False)
    limits.reserve("judge-1")  # the one question is still there
    with pytest.raises(LimitReached):
        limits.reserve("judge-1", question=False)  # nothing left to say it to
