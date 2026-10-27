"""The morning brief: every day at a set local time, to every allowed chat."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime, time, timedelta

from nazar_agent.tools.common import COMPANY_TZ

log = logging.getLogger(__name__)


def parse_brief_time(value: str) -> time | None:
    """"08:30" → 08:30; empty → no brief."""
    if not value.strip():
        return None
    hours, _, minutes = value.strip().partition(":")
    return time(int(hours), int(minutes or 0))


def seconds_until(at: time, now: datetime) -> float:
    """Seconds from ``now`` (aware) to the next ``at`` in the company's time zone."""
    local_now = now.astimezone(COMPANY_TZ)
    target = datetime.combine(local_now.date(), at, tzinfo=COMPANY_TZ)
    if target <= local_now:
        target += timedelta(days=1)
    return (target - local_now).total_seconds()


async def run_daily(at: time, job: Callable[[], Awaitable[None]]) -> None:
    while True:
        await asyncio.sleep(seconds_until(at, datetime.now(COMPANY_TZ)))
        log.info("morning brief")
        try:
            await job()
        except Exception:  # tomorrow's brief must still run
            log.exception("morning brief failed")
