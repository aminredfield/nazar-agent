"""Looking at check-in photos: the photo inspector, a role of its own.

The attendance analyst cannot see images. It names check-ins; the inspector
gets their photos from NAZAR — with the same session, so with the same
rights — and says what is in them: a live selfie or a photo of a screen or a
print, the background, which photos share a background. It describes; it
does not judge people.

Photos of people go only to the open-weights model (Muse Spark Open), which
a company can run in its own country — never to the analyst's model. The
analyst gets the inspector's words, keyed by check-in, which the fact
checker can trace like any other NAZAR record.
"""

from __future__ import annotations

import base64
from typing import Any, Literal
from uuid import UUID

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nazar_agent.engine import Tool, ToolExecutionError, ToolParams, tool_schema
from nazar_agent.llm import LLMClient
from nazar_agent.nazar import NazarApiError, NazarClient
from nazar_agent.progress import emit
from nazar_agent.tools.common import call_nazar, local_time
from nazar_agent.usage import count

MAX_PHOTOS = 8
# Muse Spark Open always reasons; the reasoning shares the output budget.
INSPECTOR_MAX_OUTPUT_TOKENS = 8_000

INSTRUCTIONS = """\
You look at check-in photos from NAZAR, a time-and-attendance service:
employees check in with a selfie from their phone. The photos are numbered in
the order given. For each photo say:
- kind: live_selfie — a person photographed live; photo_of_screen — a face
  shown on a phone or computer screen and photographed (bezel, glare, screen
  lines); photo_of_print — a printed photo; unclear — you cannot tell;
- background: a few plain words about what is behind the face;
- notes: anything else visible that matters for a check-in, or empty.
Then list groups of photos clearly taken against the same background.

Describe only what is visible. Do not guess who the person is, do not judge
anyone, do not compare faces. Write in Russian.
"""

Kind = Literal["live_selfie", "photo_of_screen", "photo_of_print", "unclear"]


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PhotoLook(_Strict):
    photo: int = Field(description="The photo number as given.")
    kind: Kind
    background: str
    notes: str


class PhotoReport(_Strict):
    photos: list[PhotoLook]
    same_background: list[list[int]] = Field(description="Groups of photo numbers with the same background.")


PHOTO_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "PhotoReport",
    "schema": tool_schema(PhotoReport),
    "strict": True,
}


class InspectPhotosParams(ToolParams):
    event_ids: list[UUID] = Field(
        min_length=1,
        max_length=MAX_PHOTOS,
        description=f"Check-in or check-out event ids from get_attendance_events, 1–{MAX_PHOTOS}.",
    )


class PhotoInspector:
    """The open-weights model that looks at photos; counts what it spent."""

    def __init__(self, llm: LLMClient, model: str) -> None:
        self.llm = llm
        self.model = model
        self.input_tokens = 0
        self.output_tokens = 0

    async def look(self, photos: list[bytes]) -> PhotoReport:
        content: list[dict[str, Any]] = [{"type": "input_text", "text": f"{len(photos)} check-in photos."}]
        for number, jpeg in enumerate(photos, start=1):
            content.append({"type": "input_text", "text": f"Photo {number}:"})
            content.append({"type": "input_image", "image_url": "data:image/jpeg;base64," + base64.b64encode(jpeg).decode()})
        turn = await self.llm.respond(
            model=self.model,
            instructions=INSTRUCTIONS,
            input=[{"role": "user", "content": content}],
            tools=[],
            text_format=PHOTO_FORMAT,
            max_output_tokens=INSPECTOR_MAX_OUTPUT_TOKENS,
            role="photos",
        )
        count("photos", self.model, turn.input_tokens, turn.output_tokens)
        self.input_tokens += turn.input_tokens
        self.output_tokens += turn.output_tokens
        try:
            report = PhotoReport.model_validate_json(turn.text)
        except ValidationError as exc:
            raise ToolExecutionError(f"The photo inspector's answer did not match the schema: {exc.error_count()} errors.") from exc
        shown = set(range(1, len(photos) + 1))
        named = {item.photo for item in report.photos} | {number for group in report.same_background for number in group}
        if not named <= shown:
            raise ToolExecutionError("The photo inspector answered about photos it was not shown.")
        return report


async def _event_or_none(nazar: NazarClient, event_id: UUID) -> dict[str, Any] | None:
    # Live: the model copied one of eight ids with a typo, and the 404 cost it
    # all eight photos. An unknown id is reported, the rest are still looked at.
    try:
        return await nazar.get(f"/attendance/events/{event_id}")
    except NazarApiError as exc:
        if exc.status_code == httpx.codes.NOT_FOUND:
            return None
        raise ToolExecutionError(f"NAZAR API error {exc.status_code}: {exc.message}") from exc


def photo_tools(nazar: NazarClient, inspector: PhotoInspector) -> tuple[Tool, ...]:
    async def inspect_photos(params: InspectPhotosParams) -> dict[str, Any]:
        found = {event_id: await _event_or_none(nazar, event_id) for event_id in params.event_ids}
        events = [event for event in found.values() if event is not None]
        result: dict[str, Any] = {
            "photos": [],
            "same_background": [],
            "without_photo": [event["id"] for event in events if not event.get("photo_url")],
            "not_found": [str(event_id) for event_id, event in found.items() if event is None],
        }
        with_photo = [event for event in events if event.get("photo_url")]
        if not with_photo:
            return result
        photos = [await call_nazar(nazar.get_bytes(event["photo_url"])) for event in with_photo]
        emit("photos", status="start", model=inspector.model, count=len(photos))
        report = await inspector.look(photos)
        by_number = {number: event for number, event in enumerate(with_photo, start=1)}
        emit(
            "photos",
            status="done",
            model=inspector.model,
            looks=[{"name": by_number[look.photo].get("employee_name"), "kind": look.kind} for look in report.photos],
            same_background=sum(1 for group in report.same_background if len(group) > 1),
        )
        # "id" is the check-in's own id: the fact checker adds these fields to
        # that event's record, so a fact may cite what the inspector saw.
        result["photos"] = [
            {
                "id": by_number[look.photo]["id"],
                "employee_name": by_number[look.photo].get("employee_name"),
                "at": local_time(by_number[look.photo].get("received_at_server")),
                "photo_kind": look.kind,
                "photo_background": look.background,
                "photo_notes": look.notes,
            }
            for look in report.photos
        ]
        result["same_background"] = [
            [by_number[number]["id"] for number in group] for group in report.same_background if len(group) > 1
        ]
        return result

    return (
        Tool(
            name="inspect_check_in_photos",
            description=(
                "Look at the photos of up to 8 check-ins: whether each is a live selfie or a photo of a "
                "screen or a print, what is in the background, and which photos share a background. "
                "Another model looks and describes; it does not judge."
            ),
            params=InspectPhotosParams,
            handler=inspect_photos,
        ),
    )
