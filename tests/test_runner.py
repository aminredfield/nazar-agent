import json

from pydantic import BaseModel, Field

from nazar_agent.engine import Role, Tool, ToolExecutionError, run_role
from tests.fakes import ScriptedLLM, call, turn


class EchoParams(BaseModel):
    text: str
    times: int = Field(ge=1, le=3)


async def echo(params: EchoParams) -> dict:
    return {"echo": params.text * params.times}


async def broken(params: EchoParams) -> dict:
    raise ToolExecutionError("NAZAR API error 404: Event not found.")


ECHO = Tool(name="echo", description="Echo text.", params=EchoParams, handler=echo)
BROKEN = Tool(name="broken", description="Always fails.", params=EchoParams, handler=broken)


def make_role(*tools: Tool, max_steps: int = 4) -> Role:
    return Role(name="test", instructions="Be brief.", tools=tools, model="m", max_steps=max_steps)


def outputs_of(request: dict) -> list[dict]:
    return [json.loads(item["output"]) for item in request["input"] if item.get("type") == "function_call_output"]


async def test_answers_without_tools():
    llm = ScriptedLLM([turn(text="All fine.")])

    run = await run_role(make_role(ECHO), llm, "How is it?")

    assert run.answer == "All fine."
    assert run.stopped == "answered"
    assert run.steps == ()
    assert llm.requests[0]["input"] == [{"role": "user", "content": "How is it?"}]
    assert run.conversation[-1] == {"role": "assistant", "content": "All fine."}


async def test_runs_tool_and_returns_result_to_model():
    llm = ScriptedLLM([
        turn(call("echo", {"text": "ab", "times": 2}), response_id="r1"),
        turn(text="Done."),
    ])

    run = await run_role(make_role(ECHO), llm, "Echo ab twice")

    assert run.answer == "Done."
    assert run.steps[0].output == {"echo": "abab"}
    # The whole conversation goes every time: question, the call, its result.
    question, call_item, output_item = llm.requests[1]["input"]
    assert question == {"role": "user", "content": "Echo ab twice"}
    assert call_item == {"type": "function_call", "call_id": "call_echo", "name": "echo",
                         "arguments": '{"text": "ab", "times": 2}'}
    assert output_item["type"] == "function_call_output" and output_item["call_id"] == "call_echo"
    assert json.loads(output_item["output"]) == {"echo": "abab"}
    assert run.model_turns == 2
    assert (run.input_tokens, run.output_tokens) == (20, 10)


async def test_parallel_calls_each_get_a_result_in_order():
    llm = ScriptedLLM([
        turn(
            call("echo", {"text": "a", "times": 1}, call_id="c1"),
            call("echo", {"text": "b", "times": 1}, call_id="c2"),
        ),
        turn(text="ok"),
    ])

    run = await run_role(make_role(ECHO), llm, "two")

    assert [step.call_id for step in run.steps] == ["c1", "c2"]
    assert outputs_of(llm.requests[1]) == [{"echo": "a"}, {"echo": "b"}]


async def test_bad_arguments_go_back_to_model_as_error():
    llm = ScriptedLLM([
        turn(call("echo", "{not json"), call("echo", {"text": "a", "times": 9}, call_id="c2")),
        turn(text="Sorry."),
    ])

    run = await run_role(make_role(ECHO), llm, "x")

    first, second = run.steps
    assert first.error.startswith("Arguments are not valid JSON")
    assert second.error.startswith("Invalid arguments: times:")
    assert all("error" in output for output in outputs_of(llm.requests[1]))


async def test_unknown_tool_and_tool_failure_are_reported_not_raised():
    llm = ScriptedLLM([
        turn(call("delete_everything", {}), call("broken", {"text": "a", "times": 1}, call_id="c2")),
        turn(text="Could not."),
    ])

    run = await run_role(make_role(ECHO, BROKEN), llm, "x")

    assert run.steps[0].error == "Unknown tool 'delete_everything'."
    assert run.steps[1].error == "NAZAR API error 404: Event not found."
    assert run.answer == "Could not."


async def test_stops_when_step_budget_is_spent():
    endless = [turn(call("echo", {"text": "a", "times": 1})) for _ in range(2)]
    llm = ScriptedLLM(endless)

    run = await run_role(make_role(ECHO, max_steps=2), llm, "loop")

    assert run.stopped == "max_steps"
    assert run.answer == ""
    assert len(run.steps) == 2


async def test_instructions_and_tools_sent_on_every_turn():
    llm = ScriptedLLM([turn(call("echo", {"text": "a", "times": 1})), turn(text="ok")])

    await run_role(make_role(ECHO), llm, "x")

    for request in llm.requests:
        assert request["instructions"] == "Be brief."
        assert [tool["name"] for tool in request["tools"]] == ["echo"]
