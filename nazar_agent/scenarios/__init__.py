"""Staged scenes for the new abilities, played on their own page (/scenarios)."""

from nazar_agent.scenarios.play import (
    Roster,
    ScenarioError,
    ask,
    check,
    count_shift,
    read_roster,
    roster_plan,
    same_place,
)
from nazar_agent.scenarios.stage import EXPLAIN, HEADCOUNT, PLACE_GPS, PLACES, ROSTER

__all__ = [
    "EXPLAIN", "HEADCOUNT", "PLACES", "PLACE_GPS", "ROSTER", "Roster", "ScenarioError",
    "ask", "check", "count_shift", "read_roster", "roster_plan", "same_place",
]
