"""Proposals and the audit trail, in SQLite.

A proposal is one validated action waiting for a human: pending → executed
→ undone, or pending → declined. Status changes are compare-and-set
(``transition``): a proposal moves only from the status the caller expects,
so a double click cannot execute an action twice.

The audit trail is append-only — this module has no way to change or delete
an entry. Every step is there: proposed, confirmed, executed, declined with
the reason, refused because the data changed, failed, undone.
"""

from __future__ import annotations

import json
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

ProposalStatus = Literal["pending", "executing", "executed", "declined", "stale", "failed", "undoing", "undone"]
AuditEvent = Literal[
    "proposed", "executed", "declined", "refused", "failed", "undone",
    # Importing a list of employees (nazar_agent.onboarding).
    "draft_created", "employee_created", "photo_rejected", "draft_discarded",
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS proposals (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    action TEXT NOT NULL,
    level TEXT NOT NULL,
    params TEXT NOT NULL,
    preview TEXT,
    context TEXT NOT NULL,
    card TEXT NOT NULL,
    question TEXT NOT NULL,
    result TEXT
);
CREATE TABLE IF NOT EXISTS audit (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    event TEXT NOT NULL,
    actor TEXT NOT NULL,
    details TEXT NOT NULL
);
"""


@dataclass(frozen=True, slots=True)
class Proposal:
    id: str
    created_at: str
    updated_at: str
    status: ProposalStatus
    action: str
    level: str
    params: dict[str, Any]
    preview: dict[str, Any] | None
    context: dict[str, Any]  # confidence and skeptic verdict the policy was checked against
    card: dict[str, Any]  # what the human saw: title, subject, facts
    question: str
    result: dict[str, Any] | None  # what execution returned; undo needs it


@dataclass(frozen=True, slots=True)
class AuditEntry:
    seq: int
    at: str
    proposal_id: str
    event: AuditEvent
    actor: str
    details: dict[str, Any]


class ProposalStore:
    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self.path = self._path
        # executescript commits on its own, so it runs outside a Transaction.
        connection = sqlite3.connect(self._path)
        try:
            connection.executescript(_SCHEMA)
        finally:
            connection.close()

    def add(
        self,
        *,
        action: str,
        level: str,
        params: dict[str, Any],
        preview: dict[str, Any] | None,
        context: dict[str, Any],
        card: dict[str, Any],
        question: str,
        actor: str,
    ) -> Proposal:
        now = _now()
        proposal_id = uuid.uuid4().hex
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO proposals VALUES (?, ?, ?, 'pending', ?, ?, ?, ?, ?, ?, ?, NULL)",
                (proposal_id, now, now, action, level, _dump(params), _dump(preview), _dump(context), _dump(card), question),
            )
            _append(connection, proposal_id, "proposed", actor, {"action": action, "params": params, "preview": preview})
        return self.get(proposal_id)

    def get(self, proposal_id: str) -> Proposal:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM proposals WHERE id = ?", (proposal_id,)).fetchone()
        if row is None:
            raise KeyError(proposal_id)
        return _proposal(row)

    def transition(
        self,
        proposal_id: str,
        *,
        expected: ProposalStatus,
        to: ProposalStatus,
        result: dict[str, Any] | None = None,
        audit: tuple[AuditEvent, str, dict[str, Any]] | None = None,
    ) -> Proposal | None:
        """Move from ``expected`` to ``to`` atomically; None if the status was different."""
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE proposals SET status = ?, updated_at = ?, result = COALESCE(?, result) WHERE id = ? AND status = ?",
                (to, _now(), _dump(result) if result is not None else None, proposal_id, expected),
            )
            if cursor.rowcount != 1:
                return None
            if audit is not None:
                event, actor, details = audit
                _append(connection, proposal_id, event, actor, details)
        return self.get(proposal_id)

    def record(self, proposal_id: str, event: AuditEvent, actor: str, details: dict[str, Any]) -> None:
        with self._connect() as connection:
            _append(connection, proposal_id, event, actor, details)

    def audit(self, *, limit: int = 100, proposal_id: str | None = None) -> list[AuditEntry]:
        query = "SELECT * FROM audit"
        args: tuple[Any, ...] = ()
        if proposal_id is not None:
            query += " WHERE proposal_id = ?"
            args = (proposal_id,)
        query += " ORDER BY seq DESC LIMIT ?"
        with self._connect() as connection:
            rows = connection.execute(query, (*args, limit)).fetchall()
        return [
            AuditEntry(row["seq"], row["at"], row["proposal_id"], row["event"], row["actor"], json.loads(row["details"]))
            for row in rows
        ]

    def _connect(self) -> Transaction:
        return Transaction.open(self._path)


class Transaction:
    """sqlite3's own context manager commits but does not close; this one does both."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    @classmethod
    def open(cls, path: str) -> Transaction:
        connection = sqlite3.connect(path, isolation_level=None)  # transactions are explicit
        connection.row_factory = sqlite3.Row
        return cls(connection)

    def __enter__(self) -> sqlite3.Connection:
        self._connection.execute("BEGIN IMMEDIATE")
        return self._connection

    def __exit__(self, exc_type: object, *rest: object) -> None:
        try:
            self._connection.execute("ROLLBACK" if exc_type else "COMMIT")
        finally:
            self._connection.close()


def _append(connection: sqlite3.Connection, proposal_id: str, event: AuditEvent, actor: str, details: dict[str, Any]) -> None:
    connection.execute(
        "INSERT INTO audit (at, proposal_id, event, actor, details) VALUES (?, ?, ?, ?, ?)",
        (_now(), proposal_id, event, actor, _dump(details)),
    )


def _proposal(row: sqlite3.Row) -> Proposal:
    return Proposal(
        id=row["id"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        status=row["status"],
        action=row["action"],
        level=row["level"],
        params=json.loads(row["params"]),
        preview=json.loads(row["preview"]) if row["preview"] else None,
        context=json.loads(row["context"]),
        card=json.loads(row["card"]),
        question=row["question"],
        result=json.loads(row["result"]) if row["result"] else None,
    )


def _dump(value: Any) -> str | None:
    return None if value is None else json.dumps(value, ensure_ascii=False, default=str)


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
