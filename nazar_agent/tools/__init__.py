from datetime import datetime

from nazar_agent.engine import Tool
from nazar_agent.nazar import NazarClient
from nazar_agent.tools.attendance import attendance_tools
from nazar_agent.tools.facts import fact_tools
from nazar_agent.tools.photos import PhotoInspector, photo_tools
from nazar_agent.tools.timesheet import timesheet_tools


def analyst_tools(analyst: str, nazar: NazarClient, photos: PhotoInspector, now: datetime) -> tuple[Tool, ...]:
    """An analyst's tools: both get today, a period's tallies and name search counted by code;
    the attendance analyst also asks the photo inspector and reads a day in the timesheet."""
    facts = fact_tools(nazar, now)
    if analyst == "attendance":
        day = tuple(tool for tool in timesheet_tools(nazar) if tool.name == "get_timesheet_day")
        return attendance_tools(nazar) + photo_tools(nazar, photos) + facts + day
    return timesheet_tools(nazar) + facts


__all__ = ["PhotoInspector", "analyst_tools", "attendance_tools", "fact_tools", "photo_tools", "timesheet_tools"]
