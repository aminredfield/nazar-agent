"""Attendance analyst: reads check-ins and anti-fraud signals, never writes."""

from __future__ import annotations

from datetime import date

from nazar_agent.engine import Role, Tool
from nazar_agent.roles.common import ANALYST_RULES

INSTRUCTIONS = """\
You are the attendance analyst of NAZAR, a time-and-attendance service where
employees check in with their phone: face recognition, the site's geofence and
GPS anti-spoofing checks. You help the owner of a company understand what
really happened on their sites: who checked in where and when, and which
check-ins are worth a second look.

"""


def attendance_analyst(*, model: str, tools: tuple[Tool, ...], today: date) -> Role:
    return Role(
        name="attendance_analyst",
        instructions=INSTRUCTIONS + ANALYST_RULES.format(today=today.isoformat()),
        tools=tools,
        model=model,
    )
