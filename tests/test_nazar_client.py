import httpx
import pytest

from nazar_agent.nazar import NazarApiError, NazarClient


def ok(data):
    return httpx.Response(200, json={"request_id": "r", "success": True, "data": data})


def fail(status, message, code="ERROR"):
    return httpx.Response(
        status,
        json={"request_id": "r", "success": False, "error": {"code": code, "message": message}},
    )


class FakeNazar:
    """Routes requests to handlers and remembers what was called."""

    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        key = f"{request.method} {request.url.path}"
        self.calls.append(key)
        return self.routes[key](request)

    def client(self) -> NazarClient:
        return NazarClient(
            base_url="http://nazar.test/api/",
            email="agent@demo.uz",
            password="secret",
            transport=httpx.MockTransport(self),
        )


async def test_logs_in_once_and_unwraps_data():
    nazar = FakeNazar({
        "POST /api/auth/login": lambda request: ok({"user": {}}),
        "GET /api/objects": lambda request: ok([{"id": "o1"}]),
    })
    client = nazar.client()

    assert await client.get("/objects") == [{"id": "o1"}]
    assert await client.get("/objects") == [{"id": "o1"}]
    assert nazar.calls.count("POST /api/auth/login") == 1


async def test_none_params_are_not_sent():
    seen = {}

    def objects(request):
        seen.update(request.url.params)
        return ok([])

    nazar = FakeNazar({"POST /api/auth/login": lambda r: ok({}), "GET /api/objects": objects})

    await nazar.client().get("/objects", {"a": 1, "b": None})

    assert seen == {"a": "1"}


async def test_expired_session_is_refreshed_and_request_retried():
    responses = iter([fail(401, "Token expired"), ok({"total": 3})])
    nazar = FakeNazar({
        "POST /api/auth/login": lambda r: ok({}),
        "POST /api/auth/refresh": lambda r: ok({}),
        "GET /api/attendance/presence": lambda r: next(responses),
    })

    assert await nazar.client().get("/attendance/presence") == {"total": 3}
    assert nazar.calls == [
        "POST /api/auth/login",
        "GET /api/attendance/presence",
        "POST /api/auth/refresh",
        "GET /api/attendance/presence",
    ]


async def test_failed_refresh_falls_back_to_login():
    responses = iter([fail(401, "Token expired"), ok([])])
    nazar = FakeNazar({
        "POST /api/auth/login": lambda r: ok({}),
        "POST /api/auth/refresh": lambda r: fail(401, "Refresh expired"),
        "GET /api/objects": lambda r: next(responses),
    })

    await nazar.client().get("/objects")

    assert nazar.calls.count("POST /api/auth/login") == 2


async def test_api_error_keeps_message_and_code():
    nazar = FakeNazar({
        "POST /api/auth/login": lambda r: ok({}),
        "GET /api/attendance/events/x": lambda r: fail(404, "Event not found.", code="NOT_FOUND"),
    })

    with pytest.raises(NazarApiError) as error:
        await nazar.client().get("/attendance/events/x")

    assert (error.value.status_code, error.value.message, error.value.code) == (
        404,
        "Event not found.",
        "NOT_FOUND",
    )


async def test_wrong_password_is_an_error():
    nazar = FakeNazar({"POST /api/auth/login": lambda r: httpx.Response(401, json={"detail": "Bad credentials"})})

    with pytest.raises(NazarApiError, match="Bad credentials"):
        await nazar.client().get("/objects")


async def test_as_the_person_it_sends_their_token_and_never_logs_in():
    seen = []

    def route(request):
        seen.append((request.url.path, request.headers.get("authorization")))
        if request.url.path.endswith("/objects"):
            return ok([])
        return fail(401, "Token has expired.")

    client = NazarClient(base_url="http://nazar.test/api", access_token="person-token", transport=httpx.MockTransport(route))

    assert await client.get("/objects") == []
    with pytest.raises(NazarApiError) as expired:
        await client.get("/timesheet")

    assert seen == [("/api/objects", "Bearer person-token"), ("/api/timesheet", "Bearer person-token")]
    assert expired.value.code == "SESSION_EXPIRED" and "откройте помощника" in expired.value.message
