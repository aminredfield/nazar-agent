"""Recorded first clicks: one real run per heavy card per demo day (nazar_agent.service.recorded)."""

import asyncio
from types import SimpleNamespace

from nazar_agent.service.recorded import RecordedRuns


class FakeAssistants:
    """A hub whose assistants answer instantly — or refuse, when the model is 'down'."""

    def __init__(self, fail: bool = False) -> None:
        self.sessions: list[str] = []
        self.asked: list[tuple[str, str]] = []
        self.fail = fail

    def for_visitor(self, session_id: str):
        self.sessions.append(session_id)
        hub = self

        class FakeAssistant:
            progress = None

            async def ask(self, question, analyst=None, **kwargs):
                hub.asked.append((question, kwargs.get("lang")))
                if hub.fail:
                    raise RuntimeError("model down")
                self.progress = SimpleNamespace(
                    events=[{"seq": 1, "t": 0.1, "kind": "route"}, {"seq": 2, "t": 5.0, "kind": "finished", "ok": True}]
                )
                return SimpleNamespace(view={"summary": "s", "cards": []})

        return FakeAssistant()


async def test_a_recording_is_made_once_and_served_to_every_click():
    hub = FakeAssistants()
    runs = RecordedRuns(hub, lambda: "2026-11-02", {"fake": "Who really comes?"})

    await runs.warm()
    first = await runs.get("fake")
    second = await runs.get("fake")

    assert first["question"] == "Who really comes?" and len(first["events"]) == 2
    assert first["view"]["recorded"] is True and "day" not in first
    assert second == first
    assert hub.asked == [("Who really comes?", "en")]  # served from the recording, not re-run


async def test_a_stale_day_runs_live_once_and_records_again_in_the_background():
    day = {"value": "2026-11-02"}
    hub = FakeAssistants()
    runs = RecordedRuns(hub, lambda: day["value"], {"fake": "Q"})
    await runs.warm()

    day["value"] = "2026-11-03"
    stale = await runs.get("fake")
    await asyncio.gather(*runs._tasks)
    fresh = await runs.get("fake")

    assert stale is None  # that click runs live; the new recording is being made
    assert fresh is not None and len(hub.asked) == 2
    assert hub.sessions == ["recording:fake:2026-11-02", "recording:fake:2026-11-03"]


async def test_an_unknown_card_and_a_failed_recording_only_mean_a_live_run():
    broken = RecordedRuns(FakeAssistants(fail=True), lambda: "2026-11-02", {"fake": "Q"})

    await broken.warm()
    unknown = await broken.get("nope")
    failed = await broken.get("fake")
    await asyncio.gather(*broken._tasks)

    assert unknown is None and failed is None
