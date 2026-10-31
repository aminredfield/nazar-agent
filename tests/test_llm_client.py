"""The model client: each role thinks as long as the settings say; a dropped connection is tried again."""

import json

import pytest

import httpx

from nazar_agent.config import REASONING_EFFORT, Settings
from nazar_agent.llm import ChatCompletionsClient, MetaResponsesClient

RESPONSE = {
    "id": "resp_1", "object": "response", "created_at": 0, "model": "m", "status": "completed",
    "output": [{"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
                "content": [{"type": "output_text", "text": "{}", "annotations": []}]}],
    "parallel_tool_calls": False, "tool_choice": "auto", "tools": [],
    "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2,
              "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}},
}
CHAT = {
    "id": "c1", "object": "chat.completion", "created": 0, "model": "m",
    "choices": [{"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": "{}"}}],
    "usage": {"prompt_tokens": 1, "completion_tokens": 1, "total_tokens": 2},
}


def recording(answer):
    sent = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json=answer)

    return sent, httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_a_listed_role_sends_its_effort_and_another_lets_the_model_choose():
    sent, http = recording(RESPONSE)
    client = MetaResponsesClient(api_key="k", base_url="http://meta.test/v1", timeout_seconds=5, http_client=http,
                                 efforts={"dispatcher": "minimal"})

    await client.respond(model="m", instructions="i", input="q", tools=[], role="dispatcher")
    await client.respond(model="m", instructions="i", input="q", tools=[], role="attendance_analyst")

    assert sent[0]["reasoning"] == {"effort": "minimal"}
    assert "reasoning" not in sent[1]


async def test_the_chat_api_gets_it_as_reasoning_effort():
    sent, http = recording(CHAT)
    client = ChatCompletionsClient(api_key="k", base_url="http://open.test/v1", timeout_seconds=5, http_client=http,
                                   efforts={"skeptic": "low"})

    await client.respond(model="m", instructions="i", input="q", tools=[], role="skeptic")

    assert sent[0]["reasoning_effort"] == "low"


def test_the_settings_carry_the_measured_defaults_and_take_json_from_the_environment(monkeypatch):
    assert Settings(_env_file=None, model_api_key="k", demo_mode=True).reasoning_effort == REASONING_EFFORT

    monkeypatch.setenv("REASONING_EFFORT", '{"writer": "low"}')

    assert Settings(_env_file=None, model_api_key="k", demo_mode=True).reasoning_effort == {"writer": "low"}


async def test_a_dropped_connection_is_tried_again_and_a_timeout_is_not(monkeypatch):
    from nazar_agent.llm import client as module

    monkeypatch.setattr(module, "RECONNECT_PAUSES", (0.0, 0.0))
    calls = {"n": 0}

    def flaky(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] <= 3:  # the SDK's own retry, then ours
            raise httpx.ConnectError("dropped", request=request)
        return httpx.Response(200, json=RESPONSE)

    client = MetaResponsesClient(api_key="k", base_url="http://meta.test/v1", timeout_seconds=5,
                                 http_client=httpx.AsyncClient(transport=httpx.MockTransport(flaky)))
    turn = await client.respond(model="m", instructions="i", input="q", tools=[])

    assert turn.text == "{}" and calls["n"] == 4

    def slow(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ReadTimeout("slow", request=request)

    calls["n"] = 0
    client = MetaResponsesClient(api_key="k", base_url="http://meta.test/v1", timeout_seconds=5,
                                 http_client=httpx.AsyncClient(transport=httpx.MockTransport(slow)))
    with pytest.raises(module.APITimeoutError):
        await client.respond(model="m", instructions="i", input="q", tools=[])
    assert calls["n"] == 2  # the SDK's one retry only


async def test_a_failing_host_hands_the_call_to_the_backup_with_its_own_model_name():
    from nazar_agent.llm import WithBackup

    def down(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "overloaded"}})

    sent, http = recording(CHAT)
    primary = ChatCompletionsClient(api_key="k", base_url="http://baseten.test/v1", timeout_seconds=5,
                                    http_client=httpx.AsyncClient(transport=httpx.MockTransport(down)))
    backup = ChatCompletionsClient(api_key="k", base_url="http://fireworks.test/v1", timeout_seconds=5, http_client=http)

    turn = await WithBackup(primary, backup, "fireworks/muse-spark-open").respond(
        model="baseten/muse-spark-open", instructions="i", input="q", tools=[], role="skeptic")

    assert turn.text == "{}" and sent[0]["model"] == "fireworks/muse-spark-open"


def test_the_backup_is_on_only_with_its_key_and_the_open_model():
    from nazar_agent.llm import WithBackup

    base = {"_env_file": None, "model_api_key": "k", "demo_mode": True, "skeptic_api": "chat_completions",
            "skeptic_base_url": "https://inference.baseten.co/v1", "skeptic_model": "meta-models/muse-spark-open-1026"}
    assert isinstance(Settings(**base, skeptic_api_key="b", fireworks_api_key="f").skeptic_llm(), WithBackup)
    assert not isinstance(Settings(**base, skeptic_api_key="b").skeptic_llm(), WithBackup)
    assert not Settings(**base).skeptic_on_open_model  # no Baseten key: Muse Spark 1.2 through Meta, no backup
