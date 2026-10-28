"""What the team is doing right now, for the window to show while it works.

An answer takes a minute or two. Instead of a spinner, every agent reports
its steps as they happen: the dispatcher's choice, each tool an analyst
calls, the photo inspector, the fact checker's rounds, the skeptic's
verdicts, each action the validator accepts or refuses. A tool's arguments
and a short count of what came back are shown, not NAZAR's records; the
events go only to people of the same company, who see the answer anyway.

The current run is kept in a context variable, so the engine and the
reviewers report without being handed anything; outside a tracked run,
``emit`` does nothing.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any

MAX_EVENTS = 400
MAX_TEXT = 600

_current: ContextVar[Progress | None] = ContextVar("nazar_agent_progress", default=None)


class Progress:
    def __init__(self, question: str) -> None:
        self.question = question
        self.started = time.monotonic()
        self.events: list[dict[str, Any]] = []
        self.finished = False
        self.failed: str | None = None

    def add(self, kind: str, **data: Any) -> None:
        if len(self.events) >= MAX_EVENTS:
            return
        text = data.get("text")
        if isinstance(text, str) and len(text) > MAX_TEXT:
            data["text"] = text[:MAX_TEXT] + "…"
        self.events.append({"seq": len(self.events) + 1, "t": round(time.monotonic() - self.started, 1), "kind": kind, **data})

    def since(self, seq: int) -> list[dict[str, Any]]:
        return self.events[seq:]

    def finish(self, error: str | None = None) -> None:
        self.failed = error
        self.finished = True
        self.add("finished", ok=error is None, text=error or "")


def emit(kind: str, **data: Any) -> None:
    progress = _current.get()
    if progress is not None:
        progress.add(kind, **data)


@contextmanager
def tracking(progress: Progress) -> Iterator[Progress]:
    token = _current.set(progress)
    try:
        yield progress
    finally:
        _current.reset(token)
