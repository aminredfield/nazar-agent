"""HTTP client for the existing NAZAR API.

NAZAR is the product that existed before the hackathon; the agent uses its
public admin API as any other client would. Authentication is the regular
admin session: ``POST /auth/login`` sets httpOnly cookies, an expired access
token is renewed with ``POST /auth/refresh``, and if that fails the client
logs in again. The agent signs in as its own admin user, so NAZAR's audit log
attributes every change to the agent account.

Every NAZAR response is an envelope ``{success, data, error}``; this client
returns ``data`` or raises ``NazarApiError`` with the API's own message.
"""

from __future__ import annotations

from typing import Any

import httpx

CLIENT_MARKER = "nazar_agent"


class NazarApiError(Exception):
    def __init__(self, status_code: int, message: str, code: str | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


class NazarClient:
    def __init__(
        self,
        *,
        base_url: str,
        email: str,
        password: str,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._email = email
        self._password = password
        self._http = httpx.AsyncClient(
            base_url=base_url.rstrip("/"),
            timeout=timeout_seconds,
            transport=transport,
            # NAZAR rejects unsafe requests that carry a session cookie but no
            # Origin (a CSRF guard for browsers). A server-side client names
            # itself instead; NAZAR trusts this marker (nazar-changes/0001).
            headers={"X-Request-Origin": CLIENT_MARKER},
        )
        self._authenticated = False

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
        if not self._authenticated:
            await self.login()
        request = {"params": params, "json": json, "data": data, "files": files}
        response = await self._http.request(method, path, **request)
        if response.status_code == httpx.codes.UNAUTHORIZED:
            await self._renew_session()
            response = await self._http.request(method, path, **request)
        return _unwrap(response)

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
