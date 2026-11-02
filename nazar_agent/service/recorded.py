"""The first click on a heavy example card, answered at once.

"Fake check-ins" and "Today" make the analysts read three weeks of
check-ins — a minute or two of real work. A judge's first impression
should not be a timer, so the service records one real run of each
question per demo day and the window plays that recording instantly,
saying so, with a "Run live" button that runs the agents for real.

The recording is honest by construction: it is not written by hand but
produced by the same pipeline a live click runs, on a company built the
same way as every visitor's own. When the demo's working day rolls over
the recording is stale: the click that finds it stale runs live as
before, and a fresh recording is made in the background for the next.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

# The two cards whose investigations read the whole period, asked as the
# window's English cards ask them; the window names a recording by key.
CARDS = {
    "fake": "Who really comes to the Riverside site?",
    "today": "Who is late or absent today?",
}


class RecordedRuns:
    """One recorded run per card per demo day, shared by every visitor."""

    def __init__(self, assistants: Any, day: Callable[[], str], cards: dict[str, str] = CARDS) -> None:
        self._assistants = assistants
        self._day = day  # the demo's working day: a recording outlives neither it nor a restart
        self._cards = dict(cards)
        self._runs: dict[str, dict[str, Any]] = {}
        self._recording: set[str] = set()
        self._tasks: set[asyncio.Task[None]] = set()

    async def get(self, card: str) -> dict[str, Any] | None:
        """Today's recording, or None — then one is being made for the next click."""
        if card not in self._cards:
            return None
        today = self._day()
        run = self._runs.get(card)
        if run is not None and run["day"] == today:
            return {key: value for key, value in run.items() if key != "day"}
        if card not in self._recording:
            task = asyncio.get_running_loop().create_task(self.record(card))
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        return None

    async def warm(self) -> None:
        """Record every card, one after another — at start, so the first judge never waits."""
        for card in self._cards:
            await self.record(card)

    async def record(self, card: str) -> None:
        """One real run of the card's question, kept with its day; a failure only means the card runs live."""
        if card in self._recording:
            return
        self._recording.add(card)
        try:
            question = self._cards[card]
            today = self._day()
            # A visitor of its own, named by the day: the same freshly built
            # company every browser gets, never a company mid-judging.
            assistant = self._assistants.for_visitor(f"recording:{card}:{today}")
            answer = await assistant.ask(question, None, lang="en")
            events = list(assistant.progress.events) if assistant.progress is not None else []
            self._runs[card] = {
                "day": today,
                "question": question,
                "events": events,
                # The proposals in it live in the recording's own company: the
                # window shows them read-only and offers to run live instead.
                "view": {**answer.view, "recorded": True},
            }
            log.info("recorded %r for %s: %d events", question, today, len(events))
        except Exception:
            log.exception("recording the %r card failed; it will run live", card)
        finally:
            self._recording.discard(card)
