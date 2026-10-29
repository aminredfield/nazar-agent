"""Limits for a public demo: questions per window session and a daily budget.

Everyone who tries the demo signs in with the same demo account, so the
limit is per window session (one sign-in from the cabinet), not per person.
A new sign-in is a new session, so the per-session limit only paces a
visitor; what protects the credits is the budget in dollars across all
sessions. A question is reserved before any model runs, in one transaction,
and its cost is added afterwards even if the run failed. Both reset at local
midnight. Zero switches a limit off — a real company's assistant has none.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from datetime import date, datetime
from pathlib import Path

from nazar_agent.tools.common import COMPANY_TZ

_SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    day TEXT NOT NULL,
    session TEXT NOT NULL,
    questions INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    PRIMARY KEY (day, session)
);
"""


class LimitReached(Exception):
    """A limit stops the question; ``said(lang)`` is the message for the person in the window."""

    def __init__(self, kind: str, limit: float) -> None:
        super().__init__(MESSAGES[kind]["en"].format(limit=limit))
        self.kind, self.limit = kind, limit

    def said(self, lang: str) -> str:
        return MESSAGES[self.kind].get(lang, MESSAGES[self.kind]["en"]).format(limit=self.limit)


MESSAGES = {
    "questions": {
        "en": "This demo allows {limit} questions per visitor a day, and they are used up. Thank you for trying it!",
        "ru": "В этом демо — {limit} вопросов на посетителя в день, они закончились. Спасибо, что попробовали!",
        "uz": "Bu demoda bir kunda har bir tashrif buyuruvchiga {limit} ta savol, ular tugadi. Sinab ko'rganingiz uchun rahmat!",
    },
    "budget": {
        "en": "Today's budget for this public demo is spent. It starts again at midnight Tashkent time (UTC+5).",
        "ru": "Дневной бюджет публичного демо исчерпан. Он начнётся заново в полночь по Ташкенту (UTC+5).",
        "uz": "Ommaviy demoning bugungi byudjeti tugadi. U Toshkent vaqti bilan yarim tunda (UTC+5) qayta boshlanadi.",
    },
}


def _today() -> date:
    return datetime.now(COMPANY_TZ).date()


class Limits:
    def __init__(
        self,
        path: Path | str,
        *,
        questions_per_session: int,
        budget_usd_per_day: float,
        today: Callable[[], date] = _today,
    ) -> None:
        self._path = str(path)
        self.questions_per_session = questions_per_session
        self.budget_usd_per_day = budget_usd_per_day
        self._today = today
        with closing(sqlite3.connect(self._path)) as connection:
            connection.executescript(_SCHEMA)

    @property
    def active(self) -> bool:
        return self.questions_per_session > 0 or self.budget_usd_per_day > 0

    def reserve(self, session_id: str, *, question: bool = True) -> str:
        """Count one question for this session, or raise LimitReached; returns the day to add its cost to.

        ``question=False`` checks the same limits but counts nothing: a voice
        message turned into text is not a question yet.

        Check and count happen in one write transaction, so parallel requests
        cannot all slip under the limit.
        """
        day = self._today().isoformat()
        if not self.active:
            return day
        with self._connect() as connection:
            asked = connection.execute(
                "SELECT questions FROM usage WHERE day = ? AND session = ?", (day, _key(session_id))
            ).fetchone()
            (spent,) = connection.execute("SELECT COALESCE(SUM(cost_usd), 0) FROM usage WHERE day = ?", (day,)).fetchone()
            if self.questions_per_session and asked and asked[0] >= self.questions_per_session:
                raise LimitReached("questions", self.questions_per_session)
            if self.budget_usd_per_day and spent >= self.budget_usd_per_day:
                raise LimitReached("budget", self.budget_usd_per_day)
            connection.execute(
                "INSERT INTO usage VALUES (?, ?, ?, 0) ON CONFLICT (day, session) DO UPDATE SET questions = questions + excluded.questions",
                (day, _key(session_id), int(question)),
            )
        return day

    def add_cost(self, session_id: str, day: str, cost_usd: float) -> None:
        if not self.active:
            return
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO usage VALUES (?, ?, 0, ?) ON CONFLICT (day, session) DO UPDATE SET cost_usd = cost_usd + excluded.cost_usd",
                (day, _key(session_id), max(cost_usd, 0.0)),
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        """One write transaction per block (commit, or rollback on an error), and the file closed after."""
        with closing(sqlite3.connect(self._path, isolation_level=None)) as connection:
            connection.execute("BEGIN IMMEDIATE")
            try:
                yield connection
            except BaseException:
                connection.execute("ROLLBACK")
                raise
            connection.execute("COMMIT")


def _key(session_id: str) -> str:
    # The session id is a credential; only its hash is stored.
    return hashlib.sha256(session_id.encode()).hexdigest()[:32]
