"""Helpers shared by the NAZAR tools."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from nazar_agent.engine import ToolExecutionError
from nazar_agent.nazar import NazarApiError

# NAZAR companies work in Tashkent time; a "day" in a question means a local day.
COMPANY_TZ = ZoneInfo("Asia/Tashkent")


async def call_nazar(request: Awaitable[Any]) -> Any:
    """Await a NAZAR request; an API error or no answer becomes a tool error the model can read."""
    try:
        return await request
    except NazarApiError as exc:
        raise ToolExecutionError(f"NAZAR API error {exc.status_code}: {exc.message}") from exc
    except httpx.HTTPError as exc:  # NAZAR down or slow: a gap in the data, not a crashed answer
        raise ToolExecutionError(f"NAZAR did not answer: {type(exc).__name__}") from exc


def local_time(value: str | None) -> str | None:
    if value is None:
        return None
    return datetime.fromisoformat(value).astimezone(COMPANY_TZ).isoformat(timespec="seconds")
