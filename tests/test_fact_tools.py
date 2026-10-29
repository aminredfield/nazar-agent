"""Today, a period's tallies and names, counted by code, agree with the demo's truth."""

from datetime import date

import pytest

from nazar_agent.config import Settings
from nazar_agent.evals.questions import BANK_NOW
from nazar_agent.evals.truth import Truth
from nazar_agent.nazar.connect import connect
from nazar_agent.tools.facts import FindParams, TallyParams, TodayParams, fact_tools, similarity


@pytest.fixture
async def demo(tmp_path):
    settings = Settings(_env_file=None, model_api_key="k", demo_mode=True, demo_now=BANK_NOW, agent_db_path=str(tmp_path / "a.sqlite3"))
    link = connect(settings)
    nazar = link.client()
    yield {tool.name: tool for tool in fact_tools(nazar, BANK_NOW)}, Truth(link.demo.world)
    await nazar.aclose()


async def test_today_names_who_came_late_though_the_timesheet_has_no_lateness_yet(demo):
    tools, truth = demo

    today = await tools["get_today"].handler(TodayParams())

    late = sorted(person["name"] for person in today["people"] if person["late_minutes"])
    assert late == truth.late(truth.today) and len(late) == today["counts"]["late"] == 3
    assert [person["name"] for person in today["people"] if person["status"] == "not_arrived"] == truth.absent(truth.today)
    assert today["counts"]["on_site"] == len(truth.on_site_now())
    zuhra = next(person for person in today["people"] if person["name"] == "Zuhra Nabieva")
    assert (zuhra["shift_start"], zuhra["late_grace_minutes"], zuhra["id"]) == ("08:00", 10, f"today:{zuhra['employee_id']}")


async def test_a_period_is_counted_by_code(demo):
    tools, truth = demo
    saturday = truth.last(6)

    month = await tools["count_period"].handler(TallyParams(date_from=truth.world.first_day, date_to=truth.today))
    day = await tools["count_period"].handler(TallyParams(date_from=saturday, date_to=saturday))
    yesterday = await tools["count_period"].handler(TallyParams(date_from=truth.yesterday, date_to=truth.yesterday))

    assert month["totals"]["late_days"] == sum(truth.counts(truth.world.first_day, truth.today, "late_days").values())
    assert "today_note" in month and "today_note" not in day
    assert sorted(person["employee_name"] for person in day["by_day"][0]["late"]) == truth.late(saturday)
    assert day["by_day"][0]["weekday"] == "Saturday"
    sergeli = next(site for site in yesterday["by_site"] if "Sergeli" in site["name"])
    assert sergeli["worked_minutes"] == truth.site_worked_minutes("sergeli", truth.yesterday)


async def test_one_site_is_counted_alone(demo):
    tools, truth = demo
    yashnobod = next(site for site in truth.world.sites.values() if site.key == "yashnobod")

    week = await tools["count_period"].handler(TallyParams(date_from=date(2026, 10, 19), date_to=date(2026, 10, 25), object_id=yashnobod.id))

    assert [site["object_id"] for site in week["by_site"]] == [yashnobod.id]
    assert week["totals"]["id"].startswith(f"tally:{yashnobod.id}:")


@pytest.mark.parametrize(("query", "found"), [
    ("Тимур Хасонов", ["Timur Hasanov"]),  # a typo
    ("Rustam aka", ["Rustam Yuldashev"]),  # Latin letters, a form of address
    ("Сергели", ["Sergeli warehouse"]),
])
async def test_a_name_is_found_as_the_owner_wrote_it(demo, query, found):
    tools, _ = demo

    result = await tools["find_people_and_sites"].handler(FindParams(query=query))

    assert [match["name"] for match in result["matches"]][: len(found)] == found and not result["ambiguous"]


async def test_two_close_names_are_both_returned_to_ask_which_one(demo):
    tools, _ = demo

    result = await tools["find_people_and_sites"].handler(FindParams(query="Каримов"))
    nobody = await tools["find_people_and_sites"].handler(FindParams(query="Алишер Усманов"))

    assert {"Bobur Karimov", "Feruza Karimova"} <= {match["name"] for match in result["matches"]} and result["ambiguous"]
    assert nobody["matches"] == [] and nobody["note"]


def test_a_different_name_is_not_a_match():
    assert similarity("Алишер Усманов", "Azamat Normatov") < 0.75
