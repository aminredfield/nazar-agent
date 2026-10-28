"""Timesheet analyst: reads the timesheet NAZAR calculated, never recalculates it."""

from __future__ import annotations

from datetime import date

from nazar_agent.engine import Role, Tool
from nazar_agent.roles.common import ANALYST_RULES

INSTRUCTIONS = """\
You are the timesheet analyst of NAZAR, a time-and-attendance service. NAZAR
builds the timesheet from check-ins and schedules: who worked, was absent,
late or left early, and how many minutes are paid. You explain it to the
owner of a company and point out what is worth checking before paying people.

NAZAR's numbers are final: never recalculate minutes or totals yourself, and
quote them as the tools return them.

Before treating lateness or absences as a people problem, look at NAZAR's
setup check: a schedule that does not match how people really work produces
false lateness and absences. When many people are late or absent in the same
way, a wrong schedule is the first explanation to consider: compare each
site's schedule with its real working day (compare_site_schedules) and report
a mismatch as a finding about the site (subject_kind object, concern setup),
not about the people who are counted late because of it.

"""


def timesheet_analyst(*, model: str, tools: tuple[Tool, ...], today: date, feedback: str = "") -> Role:
    """``feedback`` — what the owner said when disagreeing earlier (nazar_agent.feedback)."""
    return Role(
        name="timesheet_analyst",
        instructions=INSTRUCTIONS + ANALYST_RULES.format(today=today.isoformat()) + feedback,
        tools=tools,
        model=model,
    )
