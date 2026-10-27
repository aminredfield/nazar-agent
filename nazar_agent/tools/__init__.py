from nazar_agent.engine import Tool
from nazar_agent.nazar import NazarClient
from nazar_agent.tools.attendance import attendance_tools
from nazar_agent.tools.photos import PhotoInspector, photo_tools
from nazar_agent.tools.timesheet import timesheet_tools


def analyst_tools(analyst: str, nazar: NazarClient, photos: PhotoInspector) -> tuple[Tool, ...]:
    """An analyst's tools; the attendance analyst can also ask the photo inspector."""
    if analyst == "attendance":
        return attendance_tools(nazar) + photo_tools(nazar, photos)
    return timesheet_tools(nazar)


__all__ = ["PhotoInspector", "analyst_tools", "attendance_tools", "photo_tools", "timesheet_tools"]
