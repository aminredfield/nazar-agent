"""Who is using the assistant window: the person who opened it from NAZAR.

The NAZAR cabinet opens the window with a one-minute pass in the URL fragment
(``#handoff=…``; a fragment never reaches a server log). The window posts it
here, and this service exchanges it at NAZAR — with the shared assistant key —
for that person's own NAZAR session. From then on every NAZAR request is made
with their rights, and every decision in the audit trail carries their name
from NAZAR, not a name typed into the window.

Sessions live in memory and end with the NAZAR token; the person opens the
assistant from the cabinet again. The browser holds only a random id in an
httpOnly cookie, never the NAZAR token.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx

COOKIE = "nazar_agent_session"


class SessionError(Exception):
    """The pass could not be exchanged; the message is for the person."""


@dataclass(frozen=True, slots=True)
class Person:
    session_id: str
    access_token: str
    expires_at: datetime
    company_id: str
    company_name: str
    full_name: str
    role: str

    @property
    def expired(self) -> bool:
        return datetime.now(UTC) >= self.expires_at


class Sessions:
    def __init__(
        self,
        *,
        nazar_base_url: str,
        agent_key: str,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._agent_key = agent_key
        self._http = httpx.AsyncClient(base_url=nazar_base_url.rstrip("/"), timeout=timeout_seconds, transport=transport)
        self._people: dict[str, Person] = {}

    async def open(self, handoff: str) -> Person:
        if not self._agent_key:
            raise SessionError("Помощник не подключён к NAZAR: не задан ключ агента.")
        response = await self._http.post(
            "/ai-agent/session",
            json={"handoff": handoff},
            headers={"X-Agent-Key": self._agent_key, "X-Request-Origin": "nazar_agent"},
        )
        body = response.json() if response.content else {}
        if response.is_error or not body.get("success", False):
            raise SessionError("Пропуск устарел или недействителен — откройте помощника из кабинета NAZAR ещё раз.")
        data = body["data"]
        self._forget_expired()
        person = Person(
            session_id=secrets.token_urlsafe(32),
            access_token=data["access_token"],
            expires_at=datetime.fromisoformat(data["expires_at"]),
            company_id=str(data["company_id"]),
            company_name=data["company_name"],
            full_name=data["full_name"],
            role=data["role"],
        )
        self._people[person.session_id] = person
        return person

    def get(self, session_id: str | None) -> Person | None:
        person = self._people.get(session_id or "")
        if person is None or person.expired:
            self._people.pop(session_id or "", None)
            return None
        return person

    async def aclose(self) -> None:
        await self._http.aclose()

    def _forget_expired(self) -> None:
        for session_id in [key for key, person in self._people.items() if person.expired]:
            del self._people[session_id]
