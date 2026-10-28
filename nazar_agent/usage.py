"""Every model call, counted: which role, which model, how many tokens, what it cost.

Each place that calls a model reports the call with ``count``. Inside a
metered run (a question, a voice message, a list to read) the calls are
collected, then written to the ledger — one row per call — so the owner and
the team can see what the agents spend, by role and by model, today, this
week and in all.

The price comes from ``llm.pricing`` as of its date. A model without a known
price (open weights on a provider) is priced high and marked as an estimate,
never counted as free.

The same calls go to NAZAR (``POST /ai-agent/usage``, with the assistant key),
where the platform's superadmin sees what the assistant spends per company
(NAZAR change 0007). A report that fails is logged, never fails the answer.
"""

from __future__ import annotations

import logging
import sqlite3
import uuid
from collections.abc import Iterator
from contextlib import closing, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Literal

import httpx

from nazar_agent.llm.pricing import budget_usd, cost_usd

Source = Literal["ask", "voice", "import", "telegram", "brief"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS model_calls (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    company TEXT NOT NULL,
    source TEXT NOT NULL,
    question TEXT NOT NULL,
    role TEXT NOT NULL,
    model TEXT NOT NULL,
    input_tokens INTEGER NOT NULL,
    output_tokens INTEGER NOT NULL,
    cost_usd REAL NOT NULL,
    estimated INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS model_calls_at ON model_calls (company, at);
"""

_current: ContextVar[Meter | None] = ContextVar("nazar_agent_meter", default=None)
log = logging.getLogger(__name__)
REPORT_TIMEOUT_SECONDS = 10.0


@dataclass(frozen=True, slots=True)
class Call:
    role: str
    model: str
    input_tokens: int
    output_tokens: int

    @property
    def estimated(self) -> bool:
        return cost_usd(self.model, self.input_tokens, self.output_tokens) is None

    @property
    def cost(self) -> float:
        known = cost_usd(self.model, self.input_tokens, self.output_tokens)
        return known if known is not None else budget_usd(self.model, self.input_tokens, self.output_tokens)


class Meter:
    """The calls of one run."""

    def __init__(self) -> None:
        self.calls: list[Call] = []

    def by_role(self) -> list[dict[str, Any]]:
        """Calls of the same role and model summed, in the order they first ran."""
        totals: dict[tuple[str, str], list[int]] = {}
        for call in self.calls:
            total = totals.setdefault((call.role, call.model), [0, 0, 0])
            total[0] += call.input_tokens
            total[1] += call.output_tokens
            total[2] += 1
        rows = []
        for (role, model), (tokens_in, tokens_out, calls) in totals.items():
            call = Call(role, model, tokens_in, tokens_out)
            rows.append({
                "role": role, "model": model, "calls": calls, "input_tokens": tokens_in, "output_tokens": tokens_out,
                "cost_usd": round(call.cost, 5), "estimated": call.estimated,
            })
        return rows

    @property
    def cost(self) -> float:
        return sum(call.cost for call in self.calls)


def count(role: str, model: str, input_tokens: int, output_tokens: int) -> None:
    meter = _current.get()
    if meter is not None:
        meter.calls.append(Call(role, model, input_tokens, output_tokens))


@contextmanager
def metering(meter: Meter) -> Iterator[Meter]:
    token = _current.set(meter)
    try:
        yield meter
    finally:
        _current.reset(token)


class Ledger:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        with closing(sqlite3.connect(self._path)) as connection:
            connection.executescript(_SCHEMA)

    def record(self, *, company: str, source: Source, question: str, calls: list[Call]) -> None:
        if not calls:
            return
        now = datetime.now(UTC).isoformat(timespec="seconds")
        with self._connect() as connection:
            connection.executemany(
                "INSERT INTO model_calls (at, company, source, question, role, model, input_tokens, output_tokens, cost_usd, estimated) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    (now, company, source, question[:300], call.role, call.model, call.input_tokens, call.output_tokens,
                     round(call.cost, 6), int(call.estimated))
                    for call in calls
                ],
            )

    def summary(self, company: str, *, now: datetime | None = None) -> dict[str, Any]:
        """What this company's assistant spent: today (UTC), the last 7 days, in all; by role and model."""
        now = now or datetime.now(UTC)
        periods = {
            "today": now.replace(hour=0, minute=0, second=0, microsecond=0).isoformat(timespec="seconds"),
            "week": (now - timedelta(days=7)).isoformat(timespec="seconds"),
            "all": "",
        }
        with self._connect() as connection:
            totals = {
                name: self._totals(connection, company, since) for name, since in periods.items()
            }
            rows = connection.execute(
                "SELECT role, model, SUM(input_tokens), SUM(output_tokens), SUM(cost_usd), MAX(estimated), COUNT(*) "
                "FROM model_calls WHERE company = ? GROUP BY role, model ORDER BY SUM(cost_usd) DESC",
                (company,),
            ).fetchall()
        return {
            **totals,
            "by_role": [
                {"role": role, "model": model, "input_tokens": tokens_in, "output_tokens": tokens_out,
                 "cost_usd": round(cost, 4), "estimated": bool(estimated), "calls": calls}
                for role, model, tokens_in, tokens_out, cost, estimated, calls in rows
            ],
        }

    @staticmethod
    def _totals(connection: sqlite3.Connection, company: str, since: str) -> dict[str, Any]:
        tokens_in, tokens_out, cost, questions, estimated = connection.execute(
            "SELECT COALESCE(SUM(input_tokens), 0), COALESCE(SUM(output_tokens), 0), COALESCE(SUM(cost_usd), 0), "
            "COUNT(DISTINCT at || question), COALESCE(MAX(estimated), 0) "
            "FROM model_calls WHERE company = ? AND at >= ?",
            (company, since),
        ).fetchone()
        return {
            "input_tokens": tokens_in,
            "output_tokens": tokens_out,
            "cost_usd": round(cost, 4),
            "runs": questions,
            "per_run_usd": round(cost / questions, 4) if questions else None,
            "estimated": bool(estimated),
        }

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        with closing(sqlite3.connect(self._path)) as connection, connection:
            yield connection


class NazarUsageReport:
    """Sends a run's calls to NAZAR, for the superadmin."""

    def __init__(self, *, base_url: str, agent_key: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = base_url.rstrip("/") + "/ai-agent/usage"
        self._key = agent_key
        self._transport = transport

    @property
    def active(self) -> bool:
        return bool(self._key)

    async def send(self, *, company_id: str, source: Source, calls: list[Call]) -> bool:
        if not self.active or not calls or not _is_uuid(company_id):
            return False
        payload = {
            "company_id": company_id,
            "run_id": uuid.uuid4().hex,
            "source": source,
            "calls": [
                {
                    "role": call.role[:40], "model": call.model[:120], "input_tokens": call.input_tokens,
                    "output_tokens": call.output_tokens, "cost_usd": round(call.cost, 6), "estimated": call.estimated,
                }
                for call in calls[:60]
            ],
        }
        try:
            async with httpx.AsyncClient(timeout=REPORT_TIMEOUT_SECONDS, transport=self._transport) as client:
                response = await client.post(self._url, json=payload, headers={"X-Agent-Key": self._key})
        except httpx.HTTPError as exc:
            log.warning("usage report to NAZAR failed: %s", exc)
            return False
        if response.is_error:
            # An older NAZAR without the endpoint, or the assistant switched off: the local ledger still has it.
            log.warning("usage report to NAZAR refused: %s", response.status_code)
            return False
        return True


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError):
        return False
    return True

