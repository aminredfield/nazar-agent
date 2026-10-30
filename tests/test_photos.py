import base64
import json

import httpx
import pytest

from nazar_agent.engine import ToolExecutionError
from nazar_agent.llm import ChatCompletionsClient
from nazar_agent.nazar import NazarClient
from nazar_agent.tools.photos import PhotoInspector, InspectPhotosParams, photo_tools
from tests.fakes import ScriptedLLM, turn

FIRST = "11111111-1111-4111-8111-111111111111"
SECOND = "22222222-2222-4222-8222-222222222222"
NO_PHOTO = "33333333-3333-4333-8333-333333333333"
JPEG = b"\xff\xd8\xff-jpeg-bytes"


def nazar() -> tuple[NazarClient, list[httpx.Request]]:
    seen: list[httpx.Request] = []
    events = {
        FIRST: {"photo_url": "/media/attendance/a.jpg", "employee_name": "Тимур", "received_at_server": "2026-10-27T02:57:10+00:00"},
        SECOND: {"photo_url": "/media/attendance/b.jpg", "employee_name": "Мохира", "received_at_server": "2026-10-27T02:58:02+00:00"},
        NO_PHOTO: {"photo_url": None, "employee_name": "Азамат", "received_at_server": "2026-10-27T02:50:00+00:00"},
    }

    def route(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path.endswith("/auth/login"):
            return httpx.Response(200, json={"success": True, "data": {}})
        if path.startswith("/api/media/attendance/"):
            return httpx.Response(200, content=JPEG, headers={"content-type": "image/jpeg"})
        event_id = path.rsplit("/", 1)[-1]
        if event_id not in events:
            return httpx.Response(404, json={"success": False, "error": {"code": "NOT_FOUND", "message": "Attendance event not found."}})
        return httpx.Response(200, json={"success": True, "data": {"id": event_id, "event_type": "check_in", **events[event_id]}})

    client = NazarClient(base_url="http://nazar.test/api", email="a", password="b", transport=httpx.MockTransport(route))
    return client, seen


def inspector_says(photos, same_background) -> ScriptedLLM:
    return ScriptedLLM([turn(text=json.dumps({"photos": photos, "same_background": same_background}))])


def look(photo, kind, background):
    return {"photo": photo, "kind": kind, "background": background, "notes": ""}


async def test_the_inspector_sees_the_photos_and_answers_about_check_ins():
    client, seen = nazar()
    llm = inspector_says(
        [look(1, "photo_of_screen", "стена с календарём"), look(2, "photo_of_screen", "стена с календарём")],
        [[1, 2]],
    )
    inspector = PhotoInspector(llm, "open")
    [tool] = photo_tools(client, inspector)

    result = await tool.handler(InspectPhotosParams(event_ids=[FIRST, SECOND, NO_PHOTO]))

    # Answers are keyed by check-in, not photo number: the fact checker adds them to the event's record.
    assert [item["id"] for item in result["photos"]] == [FIRST, SECOND]
    assert result["photos"][0]["photo_kind"] == "photo_of_screen"
    assert result["photos"][0]["photo_background"] == "стена с календарём"
    assert result["photos"][0]["employee_name"] == "Тимур"
    assert result["same_background"] == [[FIRST, SECOND]]
    assert result["without_photo"] == [NO_PHOTO]
    # The photos went to the inspector's model as images, fetched with the caller's NAZAR session.
    [message] = llm.requests[0]["input"]
    images = [part["image_url"] for part in message["content"] if part["type"] == "input_image"]
    assert images == [f"data:image/jpeg;base64,{base64.b64encode(JPEG).decode()}"] * 2
    assert any(request.url.path == "/api/media/attendance/a.jpg" for request in seen)
    assert (inspector.input_tokens, inspector.output_tokens) == (10, 5)


async def test_no_photos_no_model_call():
    client, _ = nazar()
    llm = ScriptedLLM([])
    [tool] = photo_tools(client, PhotoInspector(llm, "open"))

    result = await tool.handler(InspectPhotosParams(event_ids=[NO_PHOTO]))

    assert result["photos"] == [] and result["without_photo"] == [NO_PHOTO]
    assert llm.requests == []


async def test_a_mistyped_id_does_not_cost_the_other_photos():
    # Live: one id copied with a typo made the whole call fail with 404.
    typo = "44444444-4444-4444-8444-444444444444"
    client, _ = nazar()
    llm = inspector_says([look(1, "live_selfie", "улица")], [])
    [tool] = photo_tools(client, PhotoInspector(llm, "open"))

    result = await tool.handler(InspectPhotosParams(event_ids=[FIRST, typo]))

    assert [item["id"] for item in result["photos"]] == [FIRST]
    assert result["not_found"] == [typo]


async def test_an_answer_about_a_photo_it_was_not_shown_is_an_error():
    client, _ = nazar()
    llm = inspector_says([look(7, "live_selfie", "улица")], [])
    [tool] = photo_tools(client, PhotoInspector(llm, "open"))

    with pytest.raises(ToolExecutionError):
        await tool.handler(InspectPhotosParams(event_ids=[FIRST]))


async def test_chat_completions_client_sends_images_as_chat_parts():
    sent = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "chat-1", "object": "chat.completion", "created": 0, "model": "open",
            "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
            "usage": {"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        })

    client = ChatCompletionsClient(
        api_key="k", base_url="http://baseten.test/v1", timeout_seconds=5,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    await client.respond(
        model="open", instructions="Look.", tools=[],
        input=[{"role": "user", "content": [
            {"type": "input_text", "text": "Photo 1"},
            {"type": "input_image", "image_url": "data:image/jpeg;base64,AAA"},
        ]}],
    )

    assert sent["messages"][1] == {"role": "user", "content": [
        {"type": "text", "text": "Photo 1"},
        {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,AAA"}},
    ]}


async def test_people_and_a_day_are_enough_no_check_in_ids_to_copy(tmp_path):
    from nazar_agent.config import Settings
    from nazar_agent.evals.questions import BANK_NOW
    from nazar_agent.nazar.connect import connect

    settings = Settings(_env_file=None, model_api_key="k", demo_mode=True, demo_now=BANK_NOW, agent_db_path=str(tmp_path / "a.sqlite3"))
    link = connect(settings)
    client = link.client()
    world = link.demo.world
    left = [person for person in world.people.values() if person.kind == "left"][:2]
    llm = inspector_says([look(1, "photo_of_screen", "cabin"), look(2, "photo_of_screen", "cabin")], [[1, 2]])
    [tool] = photo_tools(client, PhotoInspector(llm, "open"))

    result = await tool.handler(InspectPhotosParams(employee_ids=[person.id for person in left], day=world.today))
    await client.aclose()

    assert sorted(item["employee_name"] for item in result["photos"]) == sorted(person.name for person in left)
    assert len(result["same_background"]) == 1


def test_something_to_look_at_is_required():
    with pytest.raises(ValueError, match="event_ids"):
        InspectPhotosParams(employee_ids=[FIRST])
