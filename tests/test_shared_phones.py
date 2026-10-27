import httpx

from nazar_agent.nazar import NazarClient
from nazar_agent.tools import attendance_tools
from nazar_agent.tools.attendance import SharedPhonesParams, shared_phones

FOREMAN, SOUL, HONEST = "emp-foreman", "emp-soul", "emp-honest"


def event(employee, phone, at, kind="check_in", event_id=None):
    return {
        "id": event_id or f"{employee}-{at}",
        "event_type": kind,
        "employee_id": employee,
        "employee_name": employee,
        "object_name": "Блок 3",
        "device_key": phone,
        "received_at_server": at,
    }


EVENTS = [
    # Before the 18th the worker checks in from their own phone.
    event(SOUL, "own-phone", "2026-10-16T02:58:00+00:00"),
    event(SOUL, "own-phone", "2026-10-17T03:01:00+00:00"),
    # From the 18th — from the foreman's phone, a minute after him.
    event(FOREMAN, "foreman-phone", "2026-10-18T02:47:00+00:00"),
    event(SOUL, "foreman-phone", "2026-10-18T02:49:00+00:00"),
    event(SOUL, "foreman-phone", "2026-10-18T12:56:00+00:00", kind="check_out"),
    event(FOREMAN, "foreman-phone", "2026-10-19T02:45:00+00:00"),
    event(SOUL, "foreman-phone", "2026-10-19T02:47:30+00:00"),
    # Someone alone on their phone is not a shared phone.
    event(HONEST, "honest-phone", "2026-10-19T03:00:00+00:00"),
]


def test_a_phone_used_by_several_people_is_summarised_with_when_each_switched_to_it():
    [phone] = shared_phones(EVENTS)

    assert phone["id"] == "foreman-phone"
    assert phone["people_count"] == 2
    assert phone["days"] == 2
    assert phone["check_ins_minutes_after_another_person"] == 2
    soul = next(person for person in phone["people"] if person["employee_id"] == SOUL)
    assert (soul["check_ins"], soul["check_outs"]) == (2, 1)
    assert soul["first_at"] == "2026-10-18T07:49:00+05:00"
    assert soul["last_on_own_or_other_phone"] == "2026-10-17T08:01:00+05:00"
    foreman = next(person for person in phone["people"] if person["employee_id"] == FOREMAN)
    assert foreman["last_on_own_or_other_phone"] is None


def test_without_device_keys_there_is_nothing_to_summarise():
    assert shared_phones([{**item, "device_key": None} for item in EVENTS]) == []


async def test_the_tool_reads_every_page_of_the_period():
    pages = [EVENTS[:5], EVENTS[5:]]
    seen = []

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/login"):
            return httpx.Response(200, json={"success": True, "data": {}})
        seen.append(dict(request.url.params))
        page = pages[int(request.url.params["offset"]) // 5] if int(request.url.params["offset"]) < 10 else []
        return httpx.Response(200, json={"success": True, "data": page})

    client = NazarClient(base_url="http://nazar.test/api", email="a", password="b", transport=httpx.MockTransport(route))
    tool = next(item for item in attendance_tools(client, page_size=5) if item.name == "get_shared_phones")

    result = await tool.handler(SharedPhonesParams(date_from="2026-10-14", date_to="2026-10-28"))

    assert [params["offset"] for params in seen] == ["0", "5"]
    assert result["events_scanned"] == len(EVENTS) and result["truncated"] is False
    assert [phone["id"] for phone in result["phones"]] == ["foreman-phone"]
