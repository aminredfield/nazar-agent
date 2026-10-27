"""Employees waiting for a photo, and creating them in NAZAR when it is taken.

NAZAR creates an employee only with a face photo — the photo is what check-in
compares against. A list from a notebook has no photos, and a model must not
try to match faces to names. So confirmed rows wait here as drafts; each one
becomes a NAZAR employee the moment a person photographs them — one by one
in the shooting queue on a phone, or by attaching a file.

Creating is: NAZAR's own quality check of the photo (a clear refusal instead
of a failed check-in later) → the employee with the photo → assignment to the
site, keeping everyone already assigned there. Each step is in the audit trail.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from nazar_agent.actions.store import ProposalStore, Transaction
from nazar_agent.engine import ToolExecutionError
from nazar_agent.nazar import NazarApiError, NazarClient
from nazar_agent.tools.common import COMPANY_TZ, call_nazar

DraftStatus = Literal["waiting", "creating", "created", "discarded"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS drafts (
    id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status TEXT NOT NULL,
    full_name TEXT NOT NULL,
    site_id TEXT,
    site_name TEXT,
    consent_signed_at TEXT,
    employee_id TEXT
);
"""


class PhotoRejected(Exception):
    """The photo will not do; the reasons are NAZAR's own, for the person."""

    def __init__(self, reasons: list[str]) -> None:
        super().__init__("; ".join(reasons))
        self.reasons = reasons


@dataclass(frozen=True, slots=True)
class Draft:
    id: str
    created_at: str
    status: DraftStatus
    full_name: str
    site_id: str | None
    site_name: str | None
    consent_signed_at: str | None
    employee_id: str | None


class DraftStore:
    def __init__(self, path: Path | str, audit: ProposalStore) -> None:
        self._path = str(path)
        self._audit = audit
        with Transaction.open(self._path) as connection:
            connection.execute(_SCHEMA)

    def add_many(
        self, rows: list[tuple[str, str | None, str | None]], consent_signed_at: date | None, actor: str
    ) -> list[Draft]:
        """(full name, site id, site name) per row; all or nothing."""
        now = _now()
        ids = [uuid.uuid4().hex for _ in rows]
        with Transaction.open(self._path) as connection:
            for draft_id, (full_name, site_id, site_name) in zip(ids, rows, strict=True):
                connection.execute(
                    "INSERT INTO drafts VALUES (?, ?, ?, 'waiting', ?, ?, ?, ?, NULL)",
                    (draft_id, now, now, full_name, site_id, site_name, consent_signed_at.isoformat() if consent_signed_at else None),
                )
        for draft_id, (full_name, _, site_name) in zip(ids, rows, strict=True):
            self._audit.record(f"draft:{draft_id}", "draft_created", actor, {"full_name": full_name, "site": site_name})
        return [self.get(draft_id) for draft_id in ids]

    def get(self, draft_id: str) -> Draft:
        with Transaction.open(self._path) as connection:
            row = connection.execute("SELECT * FROM drafts WHERE id = ?", (draft_id,)).fetchone()
        if row is None:
            raise KeyError(draft_id)
        return _draft(row)

    def waiting(self) -> list[Draft]:
        with Transaction.open(self._path) as connection:
            rows = connection.execute(
                "SELECT * FROM drafts WHERE status IN ('waiting', 'creating') ORDER BY created_at, rowid"
            ).fetchall()
        return [_draft(row) for row in rows]

    def transition(self, draft_id: str, *, expected: DraftStatus, to: DraftStatus, employee_id: str | None = None) -> bool:
        with Transaction.open(self._path) as connection:
            cursor = connection.execute(
                "UPDATE drafts SET status = ?, updated_at = ?, employee_id = COALESCE(?, employee_id) WHERE id = ? AND status = ?",
                (to, _now(), employee_id, draft_id, expected),
            )
        return cursor.rowcount == 1

    def discard(self, draft_id: str, actor: str) -> bool:
        draft = self.get(draft_id)
        if not self.transition(draft_id, expected="waiting", to="discarded"):
            return False
        self._audit.record(f"draft:{draft_id}", "draft_discarded", actor, {"full_name": draft.full_name})
        return True

    async def photograph(self, draft_id: str, photo: bytes, content_type: str, nazar: NazarClient, actor: str) -> Draft:
        """Quality check → employee in NAZAR → site assignment. Raises PhotoRejected or ToolExecutionError."""
        draft = self.get(draft_id)
        if not self.transition(draft_id, expected="waiting", to="creating"):
            raise ToolExecutionError(f"This person is already {draft.status}.")
        try:
            await _check_photo(nazar, photo, content_type)
            employee_id = await _create_employee(nazar, draft, photo, content_type)
        except PhotoRejected as exc:
            self.transition(draft_id, expected="creating", to="waiting")
            self._audit.record(f"draft:{draft_id}", "photo_rejected", actor, {"full_name": draft.full_name, "reasons": exc.reasons})
            raise
        except ToolExecutionError:
            self.transition(draft_id, expected="creating", to="waiting")
            raise

        # The employee exists now; a failed assignment must not hide that.
        assignment_error = None
        if draft.site_id:
            try:
                await _assign(nazar, draft.site_id, employee_id)
            except ToolExecutionError as exc:
                assignment_error = str(exc)
        self.transition(draft_id, expected="creating", to="created", employee_id=employee_id)
        self._audit.record(
            f"draft:{draft_id}",
            "employee_created",
            actor,
            {"full_name": draft.full_name, "employee_id": employee_id, "site": draft.site_name, "assignment_error": assignment_error},
        )
        if assignment_error:
            raise ToolExecutionError(f"Сотрудник создан, но не назначен на объект: {assignment_error}")
        return self.get(draft_id)


async def _check_photo(nazar: NazarClient, photo: bytes, content_type: str) -> None:
    try:
        result = await nazar.post_form("/face/quality-check", {}, [("photo", ("photo.jpg", photo, content_type))])
    except NazarApiError as exc:
        # Seen live: no face at all is a 422 with NAZAR's own sentence, not a result.
        if exc.status_code == 422:
            raise PhotoRejected([exc.message]) from exc
        raise ToolExecutionError(f"NAZAR API error {exc.status_code}: {exc.message}") from exc
    reasons = [reason.partition(": ")[2] or reason for reason in result.get("blocking_reasons") or []]
    if not result.get("face_detected"):
        reasons = reasons or ["Лицо не найдено."]
    if reasons:
        raise PhotoRejected(reasons)


async def _create_employee(nazar: NazarClient, draft: Draft, photo: bytes, content_type: str) -> str:
    employee = await call_nazar(
        nazar.post_form(
            "/employees",
            {"full_name": draft.full_name, "biometric_consent_signed_at": draft.consent_signed_at},
            [("photos", ("photo.jpg", photo, content_type))],
        )
    )
    return str(employee["id"])


async def _assign(nazar: NazarClient, site_id: str, employee_id: str) -> None:
    """Add one employee to a site: NAZAR replaces the whole list, so send everyone already there too."""
    current = await call_nazar(nazar.get(f"/objects/{site_id}/assignments"))
    assigned = [item["employee_id"] for item in current.get("assignments", []) if item.get("is_active", True)]
    await call_nazar(
        nazar.post(
            f"/objects/{site_id}/assignments",
            {"employee_ids": [*dict.fromkeys([*assigned, employee_id])], "start_date": datetime.now(COMPANY_TZ).date().isoformat()},
        )
    )


def _draft(row: Any) -> Draft:
    return Draft(
        id=row["id"],
        created_at=row["created_at"],
        status=row["status"],
        full_name=row["full_name"],
        site_id=row["site_id"],
        site_name=row["site_name"],
        consent_signed_at=row["consent_signed_at"],
        employee_id=row["employee_id"],
    )


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")
