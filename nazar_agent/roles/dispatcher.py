"""The dispatcher: decides which analyst a question goes to.

One small structured call, so the owner can just ask — by text or voice,
in Telegram or in the window — without choosing "attendance" or "timesheet".
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from nazar_agent.engine import tool_schema
from nazar_agent.llm import LLMClient
from nazar_agent.progress import emit
from nazar_agent.usage import count

Analyst = Literal["attendance", "timesheet"]
DEFAULT_ANALYST: Analyst = "timesheet"

INSTRUCTIONS = """\
You route a business owner's question about their company in NAZAR, a
time-and-attendance service, to one analyst:

- attendance — check-ins themselves: who checked in where and when, who is
  on site now, suspicious check-ins, fake GPS, phones, photos, sites.
- timesheet — what the check-ins add up to: hours, lateness, absences, days
  off, overtime, what to pay, what to check before paying, schedules.

When a question touches both, pick the one that answers its main point.
"""


class Route(BaseModel):
    model_config = ConfigDict(extra="forbid")

    analyst: Analyst
    reason: str = Field(description="A few words on why.")


ROUTE_FORMAT: dict[str, Any] = {
    "type": "json_schema",
    "name": "Route",
    "schema": tool_schema(Route),
    "strict": True,
}


async def route(llm: LLMClient, model: str, question: str) -> Analyst:
    """The analyst for a question; the timesheet analyst if routing fails."""
    turn = await llm.respond(
        model=model, instructions=INSTRUCTIONS, input=question, tools=[], text_format=ROUTE_FORMAT
    )
    count("dispatcher", model, turn.input_tokens, turn.output_tokens)
    try:
        chosen = Route.model_validate_json(turn.text)
    except ValidationError:
        emit("route", analyst=DEFAULT_ANALYST, text="")
        return DEFAULT_ANALYST
    emit("route", analyst=chosen.analyst, text=chosen.reason)
    return chosen.analyst
