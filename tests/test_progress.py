from nazar_agent.findings import analyze
from nazar_agent.progress import Progress, emit, tracking
from tests.fakes import ScriptedLLM, call, turn
from tests.test_pipeline import ROLE, report_json


async def test_the_window_sees_each_step_of_the_investigation():
    llm = ScriptedLLM([
        turn(call("get_events", {"limit": 5, "ids": ["a", "b"], "nested": {"x": 1}}), response_id="r1"),
        turn(text="Seen enough.", response_id="r2"),
        turn(text=report_json("6600"), response_id="r3"),
    ])
    progress = Progress("Anything odd?")

    with tracking(progress):
        await analyze(ROLE, llm, "Anything odd?")
    progress.finish()

    kinds = [event["kind"] for event in progress.events]
    tool_start = next(event for event in progress.events if event["kind"] == "tool" and event["status"] == "start")
    tool_done = next(event for event in progress.events if event["kind"] == "tool" and event["status"] == "done")
    assert kinds == ["thinking", "tool", "tool", "thinking", "note", "report", "checker", "finished"]
    assert tool_start["args"] == {"limit": 5, "ids": "2 шт."}  # short values only, never nested records
    assert tool_done["found"] == 1
    assert progress.events[-2] == {**progress.events[-2], "findings": 1, "issues": []}
    assert [event["seq"] for event in progress.since(6)] == [7, 8]


def test_outside_a_tracked_run_nothing_is_kept():
    emit("note", text="nobody listens")

    progress = Progress("q")
    progress.add("note", text="x" * 1000)

    assert len(progress.events[0]["text"]) == 601
