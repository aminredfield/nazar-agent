"""The demo NAZAR: every route the agent calls, over the demo company, in memory.

The agent talks to it exactly as to the real NAZAR — same paths, same
envelope ``{success, data, error}``, same errors — through an
``httpx.ASGITransport`` or over HTTP. Nothing leaves the process; a restart
or ``DemoNazar.reset()`` brings the company back as it was.

The demo moves with the clock: today's check-ins appear as their time comes,
and a new day starts the company over, with every change of the day before
dropped — the history always ends today.

Sign-in is real enough to exercise the agent's client: the demo owner's
e-mail and password set cookies or give a token, the assistant key opens the
service session and the one-minute pass from the "cabinet". The values are
public demo values, not secrets: this NAZAR holds only invented people.
"""

from __future__ import annotations

import io
import secrets
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from typing import Annotated, Any

from fastapi import Depends, FastAPI, File, Form, Header, Query, Request, UploadFile
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException

from nazar_agent.demo import setup as company
from nazar_agent.demo import views
from nazar_agent.demo.photos import FACES, photo
from nazar_agent.demo.timesheet import (
    Adjustment,
    Book,
    assignment_id,
    day_row,
    period_rows,
    setup_check,
)
from nazar_agent.demo.world import TZ, Person, Schedule, World, day_world, stable_id

DEMO_EMAIL = "owner@demo.nazar"
DEMO_PASSWORD = "demo"
DEMO_AGENT_KEY = "demo-agent-key"
ACCESS_TTL = timedelta(minutes=30)
HANDOFF_TTL = timedelta(minutes=1)
MAX_TIMESHEET_DAYS = 62
MAX_PHOTO_BYTES = 8 * 1024 * 1024
MIN_PHOTO_SIDE = 240
REVIEW_STATUSES = ("not_reviewed", "no_violation", "confirmed", "insufficient_data")
CLOCK = r"^([01]\d|2[0-3]):[0-5]\d$"


class NazarError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None) -> None:
        super().__init__(message)
        self.status, self.code, self.message, self.details = status, code, message, details


def not_found(what: str) -> NazarError:
    return NazarError(404, "NOT_FOUND", f"{what} not found.")


def invalid(message: str) -> NazarError:
    return NazarError(422, "VALIDATION_ERROR", message)


@dataclass
class DemoNazar:
    """The demo NAZAR's state: the world, what was changed in it, who is signed in."""

    world: World
    agent_url: str = "http://localhost:8100"
    clock: Callable[[], datetime] = lambda: datetime.now(UTC)
    book: Book = field(default_factory=Book)
    tokens: dict[str, datetime] = field(default_factory=dict)
    refresh_tokens: dict[str, datetime] = field(default_factory=dict)
    handoffs: dict[str, datetime] = field(default_factory=dict)
    uploads: dict[str, bytes] = field(default_factory=dict)
    usage: list[dict[str, Any]] = field(default_factory=list)
    updated: dict[str, datetime] = field(default_factory=dict)
    setup: company.Setup = field(default_factory=company.Setup)  # groups and their schedules at sites

    @classmethod
    def today(cls, clock: Callable[[], datetime] | None = None, **options: Any) -> DemoNazar:
        clock = clock or (lambda: datetime.now(UTC))
        return cls(day_world(clock()), clock=clock, **options)

    def tick(self) -> None:
        """Bring the demo to the clock's moment; a new local day starts it over."""
        now = self.clock()
        if now.astimezone(TZ).date() != self.world.today:
            self.reset()
        elif now > self.world.now:
            self.world.now = now

    def reset(self, world: World | None = None) -> None:
        """The company as it was: the same world built again, every change dropped."""
        self.world = world or day_world(self.clock())
        self.book = Book()
        self.uploads.clear()
        self.usage.clear()
        self.updated.clear()
        self.setup = company.Setup()

    def issue(self, ttl: timedelta = ACCESS_TTL, store: dict[str, datetime] | None = None) -> tuple[str, datetime]:
        store = self.tokens if store is None else store
        now = self.clock()
        for stale in [key for key, until in store.items() if until <= now]:
            del store[stale]  # a long demo run must not keep every token it ever gave
        token = secrets.token_urlsafe(24)
        store[token] = now + ttl
        return token, store[token]

    def valid(self, token: str | None, store: dict[str, datetime] | None = None) -> bool:
        expires = (self.tokens if store is None else store).get(token or "")
        return expires is not None and self.clock() < expires

    def person(self, employee_id: str) -> Person:
        person = self.world.people.get(employee_id)
        if person is None:
            raise not_found("Employee")
        return person


# --- Request bodies ---------------------------------------------------------


class Credentials(BaseModel):
    email: str
    password: str


class HandoffBody(BaseModel):
    handoff: str


class UsageBody(BaseModel):
    company_id: str
    run_id: str
    source: str
    calls: list[dict[str, Any]]


class ReviewBody(BaseModel):
    status: str
    comment: str | None = None


class IntervalBody(BaseModel):
    start_at: datetime
    end_at: datetime


class TargetBody(BaseModel):
    employee_id: uuid.UUID
    work_date: date
    assignment_id: uuid.UUID | None = None
    object_id: uuid.UUID | None = None


class PreviewItem(TargetBody):
    intervals: list[IntervalBody] = Field(max_length=8)


class PreviewBody(BaseModel):
    items: list[PreviewItem] = Field(min_length=1, max_length=50)


class AdjustmentBody(PreviewItem):
    reason: str = Field(min_length=1, max_length=500)


class VoidBody(TargetBody):
    reason: str = Field(min_length=1, max_length=500)


class SiteUpdate(BaseModel):
    """NAZAR's object update; the demo keeps the place and the schedule, the rest is echoed."""

    name: str | None = Field(default=None, min_length=1, max_length=200)
    address: str | None = None
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    radius_meters: float | None = Field(default=None, gt=0, le=5000)
    is_active: bool | None = None
    shift_start: str | None = Field(default=None, pattern=CLOCK)
    shift_end: str | None = Field(default=None, pattern=CLOCK)
    working_days: list[Annotated[int, Field(ge=1, le=7)]] | None = Field(default=None, min_length=1, max_length=7)
    work_schedule_mode: str | None = None


class AssignmentsBody(BaseModel):
    employee_ids: list[uuid.UUID]
    start_date: date | None = None


# --- The app ----------------------------------------------------------------


def create_app(state: DemoNazar | None = None) -> FastAPI:
    nazar = state or DemoNazar.today()
    app = FastAPI(title="Demo NAZAR", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.nazar = nazar
    _errors(app)

    @app.middleware("http")
    async def follow_the_clock(request: Request, call_next: Callable[[Request], Any]) -> Response:
        nazar.tick()
        return await call_next(request)

    def signed_in(request: Request, authorization: str | None = Header(default=None)) -> None:
        token = authorization.removeprefix("Bearer ").strip() if authorization else request.cookies.get("access_token")
        if not nazar.valid(token):
            raise NazarError(401, "UNAUTHORIZED", "Not authenticated.")

    def agent_key(x_agent_key: str | None = Header(default=None)) -> None:
        if not x_agent_key or not secrets.compare_digest(x_agent_key, DEMO_AGENT_KEY):
            raise NazarError(403, "FORBIDDEN", "Invalid assistant key.")

    def owner(credentials: Credentials) -> None:
        email_ok = secrets.compare_digest(credentials.email.strip().lower(), DEMO_EMAIL)
        if not (email_ok and secrets.compare_digest(credentials.password, DEMO_PASSWORD)):
            raise NazarError(401, "INVALID_CREDENTIALS", "Invalid email or password.")

    auth = [Depends(signed_in)]

    # Sign-in ---------------------------------------------------------------

    @app.post("/auth/login")
    async def login(credentials: Credentials) -> JSONResponse:
        owner(credentials)
        return _with_cookies(nazar, ok(views.sign_in(nazar.world, DEMO_EMAIL)))

    @app.post("/auth/refresh")
    async def refresh(request: Request) -> JSONResponse:
        if not nazar.valid(request.cookies.get("refresh_token"), nazar.refresh_tokens):
            raise NazarError(401, "UNAUTHORIZED", "Session expired.")
        return _with_cookies(nazar, ok(views.sign_in(nazar.world, DEMO_EMAIL)))

    @app.post("/ai-agent/service-session", dependencies=[Depends(agent_key)])
    async def service_session(credentials: Credentials) -> JSONResponse:
        owner(credentials)
        token, expires = nazar.issue()
        return ok(views.session(nazar.world, token, expires, DEMO_EMAIL))

    @app.post("/ai-agent/handoff", dependencies=auth)
    async def handoff() -> JSONResponse:
        token, expires = nazar.issue(HANDOFF_TTL, nazar.handoffs)
        return ok({"url": f"{nazar.agent_url.rstrip('/')}/#handoff={token}", "expires_at": views.iso(expires)})

    @app.post("/ai-agent/session", dependencies=[Depends(agent_key)])
    async def exchange(body: HandoffBody) -> JSONResponse:
        if not nazar.valid(body.handoff, nazar.handoffs):
            raise NazarError(401, "HANDOFF_INVALID", "The pass has expired or was already used.")
        nazar.handoffs.pop(body.handoff)  # one use
        token, expires = nazar.issue()
        return ok(views.session(nazar.world, token, expires, DEMO_EMAIL))

    @app.post("/ai-agent/usage", dependencies=[Depends(agent_key)])
    async def usage(body: UsageBody) -> JSONResponse:
        nazar.usage.extend({**call, "run_id": body.run_id, "source": body.source} for call in body.calls)
        return ok({"recorded": len(body.calls)})

    # Sites and people --------------------------------------------------------

    @app.get("/objects", dependencies=auth)
    async def objects(include_inactive: bool = False) -> JSONResponse:
        world = nazar.world
        return ok([
            views.site(world, item, nazar.updated.get(item.id, item.created_at))
            for item in world.sites.values() if include_inactive or item.is_active
        ])

    @app.post("/objects", dependencies=auth, status_code=201)
    async def create_object(body: company.SiteCreate) -> JSONResponse:
        site = company.create_site(nazar.world, body, nazar.clock())
        return ok(views.site(nazar.world, site, site.created_at), status=201)

    @app.delete("/objects/{object_id}", dependencies=auth)
    async def delete_object(object_id: uuid.UUID) -> JSONResponse:
        _site_or_404(nazar, str(object_id))
        company.delete_site(nazar.world, nazar.setup, str(object_id))
        return ok(None)

    @app.post("/objects/{object_id}/schedule-preview", dependencies=auth)
    async def schedule_preview(object_id: uuid.UUID, body: company.SchedulePreview) -> JSONResponse:
        return ok(company.preview(_site_or_404(nazar, str(object_id)), body, nazar.world.today))

    @app.get("/objects/{object_id}/groups", dependencies=auth)
    async def site_groups(object_id: uuid.UUID) -> JSONResponse:
        _site_or_404(nazar, str(object_id))
        return ok(company.groups_view(nazar.world, nazar.setup, str(object_id)))

    @app.post("/objects/{object_id}/groups", dependencies=auth)
    async def set_site_group(object_id: uuid.UUID, body: company.GroupSchedule) -> JSONResponse:
        _site_or_404(nazar, str(object_id))
        company.set_group(nazar.setup, str(object_id), body, nazar.clock())
        return ok(company.groups_view(nazar.world, nazar.setup, str(object_id)))

    @app.delete("/objects/{object_id}/groups/{department_id}", dependencies=auth)
    async def remove_site_group(object_id: uuid.UUID, department_id: uuid.UUID) -> JSONResponse:
        _site_or_404(nazar, str(object_id))
        company.remove_group(nazar.setup, str(object_id), str(department_id))
        return ok(company.groups_view(nazar.world, nazar.setup, str(object_id)))

    @app.get("/departments", dependencies=auth)
    async def departments() -> JSONResponse:
        return ok([company.department_view(item) for item in nazar.setup.departments.values()])

    @app.post("/departments", dependencies=auth, status_code=201)
    async def create_department(body: company.DepartmentCreate) -> JSONResponse:
        return ok(company.department_view(company.create_department(nazar.setup, body, nazar.clock())), status=201)

    @app.delete("/departments/{department_id}", dependencies=auth)
    async def delete_department(department_id: uuid.UUID) -> JSONResponse:
        company.delete_department(nazar.setup, str(department_id))
        return ok(None)

    @app.post("/departments/bulk-add", dependencies=auth)
    async def add_members(body: company.Membership) -> JSONResponse:
        return ok(company.membership(nazar.world, nazar.setup, body, add=True, today=nazar.world.today))

    @app.post("/departments/bulk-remove", dependencies=auth)
    async def remove_members(body: company.Membership) -> JSONResponse:
        return ok(company.membership(nazar.world, nazar.setup, body, add=False, today=nazar.world.today))

    @app.patch("/objects/{object_id}", dependencies=auth)
    async def update_object(object_id: uuid.UUID, body: SiteUpdate) -> JSONResponse:
        return ok(_update_site(nazar, str(object_id), body))

    @app.get("/objects/{object_id}/assignments", dependencies=auth)
    async def site_assignments(object_id: uuid.UUID) -> JSONResponse:
        _site_or_404(nazar, str(object_id))
        return ok(views.assignments(nazar.world, str(object_id)))

    @app.post("/objects/{object_id}/assignments", dependencies=auth)
    async def replace_assignments(object_id: uuid.UUID, body: AssignmentsBody) -> JSONResponse:
        return ok(_assign(nazar, str(object_id), body))

    @app.get("/employees", dependencies=auth)
    async def employees() -> JSONResponse:
        people = sorted(nazar.world.people.values(), key=lambda person: person.code)
        return ok([views.employee(nazar.world, person, company.groups_of(nazar.setup, person.id)) for person in people])

    @app.post("/employees", dependencies=auth, status_code=201)
    async def create_employee(
        full_name: Annotated[str, Form(min_length=2, max_length=200)],
        photos: Annotated[list[UploadFile], File()],
        biometric_consent_signed_at: Annotated[str | None, Form()] = None,
    ) -> JSONResponse:
        picture = await _read_photo(photos[0])
        _quality(picture, strict=True)
        return ok(_new_person(nazar, full_name.strip(), picture), status=201)

    @app.post("/face/quality-check", dependencies=auth)
    async def quality_check(photo_file: Annotated[UploadFile, File(alias="photo")]) -> JSONResponse:
        return ok(_quality(await _read_photo(photo_file), strict=False))

    # Attendance --------------------------------------------------------------

    @app.get("/attendance/logs", dependencies=auth)
    async def logs(
        limit: Annotated[int, Query(ge=1, le=500)] = 200,
        offset: Annotated[int, Query(ge=0)] = 0,
        date_from: datetime | None = None,
        date_to: datetime | None = None,
        employee_id: uuid.UUID | None = None,
        object_id: uuid.UUID | None = None,
        event_type: Annotated[str | None, Query(pattern="^(check_in|check_out)$")] = None,
        suspicious_only: bool = False,
    ) -> JSONResponse:
        world = nazar.world
        found = [
            event for event in reversed(world.events)
            if (date_from is None or event.at >= _aware(date_from))
            and (date_to is None or event.at < _aware(date_to))
            and (employee_id is None or event.employee_id == str(employee_id))
            and (object_id is None or event.object_id == str(object_id))
            and (event_type is None or event.event_type == event_type)
            and (not suspicious_only or views.suspicious(event))
        ]
        return ok([views.event_item(world, nazar.book, event) for event in found[offset:offset + limit]])

    @app.get("/attendance/events/{event_id}", dependencies=auth)
    async def event_detail(event_id: uuid.UUID) -> JSONResponse:
        return ok(views.event_detail(nazar.world, nazar.book, _event_or_404(nazar, str(event_id))))

    @app.post("/attendance/events/{event_id}/review", dependencies=auth)
    async def review(event_id: uuid.UUID, body: ReviewBody) -> JSONResponse:
        if body.status not in REVIEW_STATUSES:
            raise invalid(f"Invalid review status. Allowed: {', '.join(REVIEW_STATUSES)}.")
        event = _event_or_404(nazar, str(event_id))
        event.review_status = body.status
        event.review_comment = (body.comment or "").strip() or None
        event.reviewed_by = views.OWNER_NAME
        event.reviewed_at = nazar.clock()
        return ok(views.event_detail(nazar.world, nazar.book, event))

    @app.get("/attendance/presence", dependencies=auth)
    async def presence(
        status: Annotated[str, Query(max_length=10)] = "all",
        limit: Annotated[int, Query(ge=1, le=100)] = 24,
        offset: Annotated[int, Query(ge=0)] = 0,
    ) -> JSONResponse:
        return ok(views.presence(nazar.world, status if status in {"all", "in", "out", "absent"} else "all", limit, offset))

    # Timesheet ---------------------------------------------------------------

    @app.get("/timesheet", dependencies=auth)
    async def timesheet(date_from: date, date_to: date, employee_id: uuid.UUID | None = None) -> JSONResponse:
        if date_to < date_from:
            raise invalid("date_to is before date_from.")
        if (date_to - date_from).days >= MAX_TIMESHEET_DAYS:
            raise invalid(f"The period is longer than {MAX_TIMESHEET_DAYS} days.")
        if employee_id is not None:
            nazar.person(str(employee_id))
        rows = period_rows(nazar.world, nazar.book, date_from, date_to, str(employee_id) if employee_id else None)
        return ok({"date_from": date_from.isoformat(), "date_to": date_to.isoformat(), "is_closed": False, "days": rows})

    @app.get("/timesheet/day/detail", dependencies=auth)
    async def timesheet_day(employee_id: uuid.UUID, work_date: date) -> JSONResponse:
        return ok(views.day_detail(nazar.world, nazar.book, nazar.person(str(employee_id)), work_date))

    @app.get("/timesheet/setup-check", dependencies=auth)
    async def timesheet_setup_check() -> JSONResponse:
        return ok(setup_check(nazar.world, nazar.book))

    @app.post("/timesheet/adjustments/preview", dependencies=auth)
    async def preview(body: PreviewBody) -> JSONResponse:
        rows = []
        for item in body.items:
            person = _target(nazar, item)
            rows.append(day_row(nazar.world, nazar.book, person, item.work_date, override=_intervals(item)))
        return ok(rows)

    @app.post("/timesheet/adjustments", dependencies=auth)
    async def adjust(body: AdjustmentBody) -> JSONResponse:
        return ok(_save_adjustment(nazar, body, _intervals(body), is_void=False))

    @app.delete("/timesheet/adjustments/void/{adjustment_id}", dependencies=auth)
    async def unvoid(adjustment_id: uuid.UUID) -> JSONResponse:
        _deactivate(nazar, str(adjustment_id), is_void=True)
        return ok(None)

    @app.delete("/timesheet/adjustments/{adjustment_id}", dependencies=auth)
    async def unadjust(adjustment_id: uuid.UUID) -> JSONResponse:
        _deactivate(nazar, str(adjustment_id), is_void=False)
        return ok(None)

    @app.post("/timesheet/adjustments/void", dependencies=auth)
    async def void(body: VoidBody) -> JSONResponse:
        return ok(_save_adjustment(nazar, body, [], is_void=True))

    # Photos ------------------------------------------------------------------

    @app.get("/media/attendance/{name}", dependencies=auth)
    async def check_in_photo(name: str) -> Response:
        event = _event_or_404(nazar, name.removesuffix(".jpg"))
        return Response(photo(event.face, event.photo_kind, event.photo_seed), media_type="image/jpeg")

    @app.get("/media/persons/{name}", dependencies=auth)
    async def person_photo(name: str) -> Response:
        person = nazar.person(name.removesuffix(".jpg"))
        picture = nazar.uploads.get(person.id) or (FACES / person.face).read_bytes()
        return Response(picture, media_type="image/jpeg")

    return app


# --- Envelope and errors ------------------------------------------------------


def _envelope(success: bool, data: Any = None, error: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"request_id": f"req_{uuid.uuid4().hex[:24]}", "success": success, "data": data, "error": error, "message": None}


def ok(data: Any, status: int = 200) -> JSONResponse:
    return JSONResponse(jsonable_encoder(_envelope(True, data)), status_code=status)


def _failure(status: int, code: str, message: str, details: Any = None) -> JSONResponse:
    error = {"code": code, "message": message, "details": details}
    return JSONResponse(jsonable_encoder(_envelope(False, error=error)), status_code=status)


def _errors(app: FastAPI) -> None:
    @app.exception_handler(NazarError)
    async def nazar_error(request: Request, exc: NazarError) -> JSONResponse:
        return _failure(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
        details = [{key: value for key, value in error.items() if key != "url"} for error in exc.errors()]
        return _failure(422, "VALIDATION_ERROR", "Request validation failed.", jsonable_encoder(details, custom_encoder={Exception: str}))

    @app.exception_handler(company.SetupError)
    async def setup_error(request: Request, exc: company.SetupError) -> JSONResponse:
        code = {404: "NOT_FOUND", 409: "CONFLICT"}.get(exc.status, "VALIDATION_ERROR")
        return _failure(exc.status, code, exc.message)

    @app.exception_handler(StarletteHTTPException)
    async def http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = "NOT_FOUND" if exc.status_code == 404 else "HTTP_ERROR"
        return _failure(exc.status_code, code, str(exc.detail))


def _with_cookies(nazar: DemoNazar, response: JSONResponse) -> JSONResponse:
    access, _ = nazar.issue()
    refresh, _ = nazar.issue(timedelta(days=7), nazar.refresh_tokens)
    response.set_cookie("access_token", access, httponly=True, samesite="lax")
    response.set_cookie("refresh_token", refresh, httponly=True, samesite="lax")
    return response


# --- Behaviour ----------------------------------------------------------------


def _aware(moment: datetime) -> datetime:
    return moment if moment.tzinfo else moment.replace(tzinfo=TZ)


def _site_or_404(nazar: DemoNazar, site_id: str):
    site = nazar.world.sites.get(site_id)
    if site is None:
        raise not_found("Object")
    return site


def _event_or_404(nazar: DemoNazar, event_id: str):
    event = next((item for item in nazar.world.events if item.id == event_id), None)
    if event is None:
        raise not_found("Attendance event")
    return event


def _update_site(nazar: DemoNazar, site_id: str, body: SiteUpdate) -> dict[str, Any]:
    site = _site_or_404(nazar, site_id)
    today = nazar.world.today
    for name in ("name", "address", "latitude", "longitude", "radius_meters", "is_active"):
        value = getattr(body, name)
        if value is not None:
            setattr(site, name, value)
    current = site.schedule_on(today)
    wanted = Schedule(
        effective_from=today,
        shift_start=body.shift_start or current.shift_start,
        shift_end=body.shift_end or current.shift_end,
        working_days=tuple(sorted(set(body.working_days))) if body.working_days else current.working_days,
        mode=body.work_schedule_mode or current.mode,
    )
    if wanted.shift_start == wanted.shift_end:
        raise invalid("The shift must not start and end at the same time.")
    if (wanted.shift_start, wanted.shift_end, wanted.working_days, wanted.mode) != (
        current.shift_start, current.shift_end, current.working_days, current.mode
    ):
        # A new version from today: the past stays counted by the schedule it had.
        site.schedules = [item for item in site.schedules if item.effective_from != today] + [wanted]
        site.schedules.sort(key=lambda item: item.effective_from)
    nazar.updated[site.id] = nazar.clock()
    return views.site(nazar.world, site, nazar.updated[site.id])


def _assign(nazar: DemoNazar, site_id: str, body: AssignmentsBody) -> dict[str, Any]:
    """NAZAR replaces the site's whole list: the people sent are there from start_date, the rest leave.

    Someone who works elsewhere keeps that site too: this one is added to it.
    """
    _site_or_404(nazar, site_id)
    world = nazar.world
    wanted = {str(employee_id) for employee_id in body.employee_ids}
    unknown = wanted - world.people.keys()
    if unknown:
        raise not_found("Employee")
    start = body.start_date or world.today
    for person in world.people.values():
        extra = world.extra_sites.setdefault(person.id, {})
        here = site_id in world.sites_of(person.id)
        if person.id in wanted and not here:
            if person.site_id is None:
                person.site_id, person.assigned_from = site_id, start
            else:
                extra[site_id] = start
        elif person.id not in wanted and here:
            if person.site_id == site_id:
                person.site_id = None
            extra.pop(site_id, None)
    return views.assignments(world, site_id)


async def _read_photo(upload: UploadFile) -> bytes:
    content = await upload.read(MAX_PHOTO_BYTES + 1)
    if len(content) > MAX_PHOTO_BYTES:
        raise invalid("The photo is larger than 8 MB.")
    return content


def _quality(content: bytes, *, strict: bool) -> dict[str, Any]:
    """A plain look at the picture. The demo has no face model: any clear photo of a size passes."""
    try:
        image = Image.open(io.BytesIO(content))
        image.load()
    except (UnidentifiedImageError, OSError) as exc:
        raise invalid("No face found in the photo: the file is not a picture.") from exc
    gray = image.convert("L")
    brightness = ImageStat.Stat(gray).mean[0]
    blur = ImageStat.Stat(gray.filter(ImageFilter.FIND_EDGES)).var[0]
    too_small = min(image.size) < MIN_PHOTO_SIDE
    if strict and too_small:
        raise invalid(f"No face found in the photo: it is smaller than {MIN_PHOTO_SIDE} px.")
    quality = {
        "brightness": "too_dark" if brightness < 50 else "too_bright" if brightness > 225 else "ok",
        "blur": "blurry" if blur < 40 else "ok",
        "face_size": "too_small" if too_small else "ok",
    }
    warnings = [f"{name}: {value}" for name, value in quality.items() if value != "ok" and name != "face_size"]
    blocking = [f"face_size: The photo is smaller than {MIN_PHOTO_SIDE} px."] if too_small else []
    return {
        "face_detected": not too_small, "face_count": 0 if too_small else 1,
        "detection_confidence": 0.0 if too_small else 0.9,
        "warnings": warnings, "blocking_reasons": blocking, "quality": quality,
        "metrics": {"brightness_score": round(brightness, 2), "blur_score": round(blur, 2), "face_area_ratio": 0.0 if too_small else 0.2},
        "summary": {"status": "blocked" if blocking else "warning" if warnings else "ok", "warnings": warnings},
        "original_width": image.width, "original_height": image.height,
    }


def _new_person(nazar: DemoNazar, full_name: str, picture: bytes) -> dict[str, Any]:
    world = nazar.world
    number = len(world.people) + 1
    now = nazar.clock()
    person = Person(
        id=stable_id("person", full_name, number), name=full_name, code=f"MQ-{number:03d}", site_id=None,
        kind="worker", face="", created_at=now, assigned_from=world.today,
    )
    world.people[person.id] = person
    nazar.uploads[person.id] = picture
    return {**views.employee(world, person), "face_ready": True, "embeddings_count": 1}


def _target(nazar: DemoNazar, item: TargetBody) -> Person:
    """The employee of an adjustment, and the shift it names must be theirs."""
    person = nazar.person(str(item.employee_id))
    if item.assignment_id is not None:
        if person.site_id is None or str(item.assignment_id) != assignment_id(person):
            raise invalid("The assignment is not this employee's.")
    elif item.object_id is not None:
        if str(item.object_id) != person.site_id:
            raise invalid("The object is not this employee's.")
    else:
        raise invalid("Name the shift: assignment_id or object_id.")
    return person


def _intervals(item: PreviewItem) -> list[tuple[datetime, datetime]]:
    day_start = datetime.combine(item.work_date, time.min, tzinfo=TZ)
    intervals = sorted((_aware(entry.start_at), _aware(entry.end_at)) for entry in item.intervals)
    for start, end in intervals:
        if end <= start:
            raise invalid("An interval ends before it starts.")
        if start < day_start - timedelta(hours=12) or end > day_start + timedelta(hours=36):
            raise invalid("An interval is too far from the work date.")
    if any(previous[1] > current[0] for previous, current in zip(intervals, intervals[1:], strict=False)):
        raise invalid("Intervals overlap.")
    return intervals


def _save_adjustment(
    nazar: DemoNazar, body: TargetBody, intervals: list[tuple[datetime, datetime]], *, is_void: bool
) -> dict[str, Any]:
    person = _target(nazar, body)
    reason = body.reason.strip()  # type: ignore[attr-defined]
    if not reason:
        raise invalid("A reason is required.")
    item = Adjustment(
        id=str(uuid.uuid4()), employee_id=person.id, work_date=body.work_date, assignment_id=assignment_id(person),
        intervals=intervals, reason=reason, created_by=views.ACCOUNT_ID, created_at=nazar.clock(), is_void=is_void,
    )
    nazar.book.adjustments.append(item)
    return views.adjustment(item, None)


def _deactivate(nazar: DemoNazar, adjustment_id: str, *, is_void: bool) -> None:
    item = next(
        (entry for entry in nazar.book.adjustments if entry.id == adjustment_id and entry.is_void == is_void and entry.is_active),
        None,
    )
    if item is None:
        raise not_found("Adjustment")
    item.is_active = False  # NAZAR keeps the history; the day is counted as before


def demo_app() -> FastAPI:
    """For ``uvicorn nazar_agent.demo.server:demo_app --factory``."""
    return create_app()


__all__ = ["DEMO_AGENT_KEY", "DEMO_EMAIL", "DEMO_PASSWORD", "DemoNazar", "create_app", "demo_app"]
