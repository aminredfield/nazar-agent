"""The Company tab's snapshot reads the demo company as NAZAR keeps it, and shows what "Apply" changed."""

import pytest

from nazar_agent.company import snapshot
from nazar_agent.config import Settings
from nazar_agent.evals.questions import BANK_NOW
from nazar_agent.evals.truth import Truth
from nazar_agent.nazar.connect import connect


@pytest.fixture
async def demo(tmp_path):
    settings = Settings(_env_file=None, model_api_key="k", demo_mode=True, demo_now=BANK_NOW, agent_db_path=str(tmp_path / "a.sqlite3"))
    link = connect(settings)
    nazar = link.client()
    yield nazar, Truth(link.demo.world)
    await nazar.aclose()


async def test_the_company_is_read_as_nazar_keeps_it(demo):
    nazar, truth = demo

    company = await snapshot(nazar, BANK_NOW)

    sites = {site["name"]: site for site in company["sites"]}
    yashnobod = sites["Yashnobod residential, finishing"]
    assert (yashnobod["shift_start"], yashnobod["shift_end"]) == ("08:00", "18:00")
    assert yashnobod["people"] == ["Dilfuza Ergasheva", "Kamola Tashpulatova", "Zuhra Nabieva"]
    assert len(company["people"]) == len([person for person in truth.world.people.values() if person.is_active])
    assert len(company["week"]["dates"]) == 7 and company["week"]["dates"][-1] == company["date"]
    zuhra = next(row for row in company["week"]["rows"] if row["name"] == "Zuhra Nabieva")
    assert zuhra["late_days"] > 0 and zuhra["worked_minutes"] > 0
    late_today = sorted(person["name"] for person in company["people"] if person["late_minutes"])
    assert late_today == truth.late(truth.today)
    assert company["check_ins"] and company["check_ins"][0]["time"] >= company["check_ins"][-1]["time"]


async def test_a_schedule_change_shows_on_the_site(demo):
    nazar, _ = demo
    before = await snapshot(nazar, BANK_NOW)
    site = next(item for item in before["sites"] if item["name"].startswith("Yashnobod"))

    await nazar.patch(f"/objects/{site['id']}", json={"shift_start": "09:00", "shift_end": "19:00"})
    after = await snapshot(nazar, BANK_NOW)

    changed = next(item for item in after["sites"] if item["id"] == site["id"])
    assert (changed["shift_start"], changed["shift_end"]) == ("09:00", "19:00")
