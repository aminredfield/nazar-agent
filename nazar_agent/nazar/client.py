"""HTTP client for the existing NAZAR API.

NAZAR is the product that existed before the hackathon; the agent uses its
public admin API as any other client would, in one of two ways:

- **as the person who opened the assistant** (``access_token``): NAZAR issued
  this token in exchange for the cabinet's one-minute pass, so every request
  is checked against that person's own rights and NAZAR's audit log names
  them. It is not renewed: when it expires, the person opens the assistant
  from the cabinet again;
- **as a service account** (``email`` and ``password``), for local
  development and the Telegram bot: ``POST /auth/login`` sets httpOnly
  cookies, an expired token is renewed with ``POST /auth/refresh``, and if
  that fails the client logs in again.

Every NAZAR response is an envelope ``{success, data, error}``; this client
returns ``data`` or raises ``NazarApiError`` with the API's own message.
"""

from __future__ import annotations

from typing import Any

import httpx

CLIENT_MARKER = "nazar_agent"
MAX_FILE_BYTES = 8 * 1024 * 1024  # a check-in photo is far smaller


class NazarApiError(Exception):
    def __init__(self, status_code: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


SESSION_EXPIRED = "Сессия NAZAR истекла — откройте помощника из кабинета заново."


class NazarClient:
    def __init__(
        self,
        *,
        base_url: str,
        email: str = "",
        password: str = "",
        access_token: str | None = None,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._person = access_token is not None
        headers = {
            # NAZAR rejects unsafe requests that carry a session cookie but no
            # Origin (a CSRF guard for browsers). A server-side client names
            # itself instead; NAZAR trusts this marker (nazar-changes/0001).
            "X-Request-Origin": CLIENT_MARKER,
        }
        if access_token is not None:
            headers["Authorization"] = f"Bearer {access_token}"
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            headers=headers,
        )
        self._authenticated = self._person

    async def get(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return await self._request("GET", path, params=_drop_none(params))

    async def post(self, path: str, json: dict[str, Any] | None = None) -> Any:
        return await self._request("POST", path, json=json)

    async def delete(self, path: str) -> Any:
        return await self._request("DELETE", path)

    async def post_form(
        self, path: str, data: dict[str, Any], files: list[tuple[str, tuple[str, bytes, str]]]
    ) -> Any:
        """multipart/form-data, e.g. an employee with a photo."""
        return await self._request("POST", path, data=_drop_none(data), files=files)

    async def get_bytes(self, path: str) -> bytes:
        """A stored file, e.g. a check-in photo (``/media/...``), with the same session."""
        response = await self._send("GET", path)
        if response.is_error:
            _unwrap(response)  # raises NazarApiError with NAZAR's message
        if len(response.content) > MAX_FILE_BYTES:
            raise NazarApiError(response.status_code, f"The file is larger than {MAX_FILE_BYTES} bytes.")
        return response.content

    async def login(self) -> None:
        response = await self._http.post(
            "/auth/login",
            json={"email": self._email, "password": self._password},
        )
        _unwrap(response)
        self._authenticated = True

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
        files: list[tuple[str, tuple[str, bytes, str]]] | None = None,
    ) -> Any:
        return _unwrap(await self._send(method, path, params=params, json=json, data=data, files=files))

    async def _send(self, method: str, path: str, **request: Any) -> httpx.Response:
        if not self._authenticated:
            await self.login()
        response = await self._http.request(method, path, **request)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            if self._person:
                raise NazarApiError(httpx.codes.UNAUTHORIZED, SESSION_EXPIRED, "SESSION_EXPIRED")
            await self._renew_session()
            response = await self._http.request(method, path, **request)
        return response

    async def _renew_session(self) -> None:
        refresh = await self._http.post("/auth/refresh")
        if refresh.is_success:
            return
        # The refresh cookie is gone or expired too — start a new session.
        self._authenticated = False
        await self.login()


def _unwrap(response: httpx.Response) -> Any:
    try:
        body = response.json()
    except ValueError:
        body = None

    if response.is_error or (isinstance(body, dict) and body.get("success") is False):
        code, message = _error_of(body)
        raise NazarApiError(
            status_code=response.status_code,
            message=message or f"NAZAR API returned HTTP {response.status_code}.",
            code=code,
        )
    if isinstance(body, dict) and "data" in body:
        return body["data"]
    return body


def _error_of(body: Any) -> tuple[str | None, str | None]:
    if not isinstance(body, dict):
        return None, None
    error = body.get("error")
    if isinstance(error, dict):
        return error.get("code"), error.get("message")
    detail = body.get("detail")
    if isinstance(detail, str):
        return None, detail
    return None, body.get("message")


def _drop_none(params: dict[str, Any] | None) -> dict[str, Any] | None:
    if params is None:
        return None
    return {key: value for key, value in params.items() if value is not None}
