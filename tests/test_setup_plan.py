"""A setup request becomes a draft and a plan of changes, on the demo company; nothing is written."""

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from nazar_agent.actions import ProposalStore
from nazar_agent.assistant import Assistant
from nazar_agent.config import Settings
from nazar_agent.nazar.connect import connect
from nazar_agent.setup.draft import Draft
from nazar_agent.setup.plan import build
from nazar_agent.setup.reading import NOTHING, SetupReading, merge, point_from
from nazar_agent.setup.view import setup_view, summary
from nazar_agent.setup.wording import days_text
from nazar_agent.tools.common import call_nazar
from tests.fakes import ScriptedLLM, turn
from tests.test_dispatcher import reading as dispatcher_reading

NOW = datetime(2026, 10, 28, 11, 30, tzinfo=ZoneInfo("Asia/Tashkent"))
OLMAZOR = "https://yandex.uz/maps/10335/tashkent/?ll=69.203411%2C41.285612&z=17"


def asked(**values) -> SetupReading:
    return NOTHING.model_copy(update=values)


@pytest.fixture
async def company(tmp_path):
    settings = Settings(_env_file=None, model_api_key="k", demo_mode=True, demo_now=NOW, agent_db_path=str(tmp_path / "a.sqlite3"))
    link = connect(settings)
    nazar = link.client()
    data = {
        "employees": await call_nazar(nazar.get("/employees")),
        "sites": await call_nazar(nazar.get("/objects")),
        "groups": await call_nazar(nazar.get("/departments")),
    }
    yield nazar, data, link.demo
    await nazar.aclose()


OPEN_OLMAZOR = asked(
    site_name="ЖК Олмазор", address="Ташкент, Чиланзар", shift_start="08:00", shift_end="17:00",
    working_days=[1, 2, 3, 4, 5, 6], group_name="бригада Рустама", people=["Рустам", "Бобур Каримов"],
)


async def test_a_new_site_with_a_crew_becomes_a_ready_plan_in_words(company):
    nazar, data, demo = company
    draft = merge(Draft.new("ru"), OPEN_OLMAZOR, await point_from(f"Открываем объект {OLMAZOR}"), **data)

    plan = await build(draft, nazar, NOW.date())
    shown = setup_view(draft, plan, "ru")

    assert [step.kind for step in plan.steps] == ["create_site", "create_group", "group_on_site", "add_to_group", "assign"]
    assert plan.ready and shown["missing"] == []
    assert shown["steps"][0]["text"] == (
        "Создам объект «ЖК Олмазор», Ташкент, Чиланзар: точка 41.28561, 69.20341, радиус 100 м; смена 08:00–17:00, пн–сб."
    )
    assert shown["steps"][3]["text"] == "Добавлю в группу «Бригада Рустама»: Рустам Юлдашев, Бобур Каримов."
    assert shown["steps"][4]["text"] == "Назначу на объект «ЖК Олмазор» с 28.10: Рустам Юлдашев, Бобур Каримов."
    assert [day["working"] for day in shown["preview"]] == [True, True, True, True, False, True, True]  # from Wednesday
    assert summary(plan, "ru").startswith("Подготовил план: шагов — 5")
    assert not any(site.name == "ЖК Олмазор" for site in demo.world.sites.values())  # nothing written yet


async def test_people_already_working_elsewhere_at_the_same_hours_are_found(company):
    nazar, data, _ = company
    draft = merge(Draft.new("ru"), OPEN_OLMAZOR, await point_from(OLMAZOR), **data)

    plan = await build(draft, nazar, NOW.date())
    shown = setup_view(draft, plan, "ru")

    rustam = next(item for item in shown["clashes"] if item["name"] == "Рустам Юлдашев")
    assert "уже работает на объекте" in rustam["text"] and "08:00–18:00" in rustam["text"] and "(08:00–17:00)" in rustam["text"]
    assert [option["kind"] for option in rustam["options"]] == ["shift", "shift", "skip"]  # no move yet
    assert rustam["options"][0]["shift"]["start"] == "18:00"


async def test_what_is_missing_is_asked_not_guessed(company):
    nazar, data, _ = company
    draft = merge(Draft.new("ru"), asked(site_name="Склад Сергели", people=["Каримов", "Жасур Тоиров"]), None, **data)

    plan = await build(draft, nazar, NOW.date())
    shown = setup_view(draft, plan, "en")

    assert [item["kind"] for item in shown["missing"]] == ["point", "shift", "choose_person"]
    assert not plan.ready and summary(plan, "ru").startswith("Готовлю план")
    karimov = draft.person("Каримов")
    assert {name for _, name in karimov.choices} == {"Бобур Каримов", "Феруза Каримова"}
    assert draft.person("Жасур Тоиров").state == "new"
    assert plan.steps[-1].kind == "wait_for_photo" and "Жасур Тоиров" in shown["steps"][-1]["text"]


async def test_a_follow_up_changes_the_draft_it_continues(company):
    nazar, data, _ = company
    draft = merge(Draft.new("ru"), OPEN_OLMAZOR, await point_from(OLMAZOR), **data)

    draft = merge(draft, asked(shift_start="09:00", shift_end="18:00", leave_out=["Бобура"]), None, **data)
    plan = await build(draft, nazar, NOW.date())

    assert (draft.shift.start, draft.shift.end, draft.shift.days) == ("09:00", "18:00", (1, 2, 3, 4, 5, 6))
    assert draft.site.name == "ЖК Олмазор" and draft.site.has_point  # kept
    assert draft.person("Бобур Каримов").state == "skip"
    assert plan.steps[-1].params["names"] == ["Рустам Юлдашев"]


async def test_a_group_on_an_existing_site_gets_its_own_shift_and_nazars_preview(company):
    nazar, data, demo = company
    yashnabad = next(site for site in demo.world.sites.values() if site.key == "yashnobod")
    draft = merge(Draft.new("ru"), asked(
        existing_site=yashnabad.name, group_name="Отделка, вторая смена", shift_start="14:00", shift_end="22:00", working_days=[1, 2, 3, 4, 5],
    ), None, **data)

    plan = await build(draft, nazar, NOW.date())
    shown = setup_view(draft, plan, "uz")

    assert [step.kind for step in plan.steps] == ["create_group", "group_on_site"]
    assert plan.steps[1].params["schedule_mode"] == "custom" and plan.steps[1].params["object_id"] == yashnabad.id
    assert shown["steps"][1]["text"].endswith("o'z smenasi bilan qo'yaman: 14:00–22:00, Du–Ju.")
    assert shown["preview"][0] == {"date": "2026-10-28", "working": True, "from": "14:00", "to": "22:00"}


async def test_a_link_without_a_point_is_said_so():
    found = await point_from("вот ссылка https://example.com/place")

    draft = merge(Draft.new("ru"), NOTHING, found, employees=[], sites=[], groups=[])

    assert draft.link_problem == "not_a_map"


def test_days_read_as_ranges_in_three_languages():
    assert days_text([1, 2, 3, 4, 5, 6], "ru") == "пн–сб"
    assert days_text([1, 3, 5], "en") == "Mon, Wed, Fri"
    assert days_text([1, 2, 6, 7], "uz") == "Du, Se, Sh, Ya"
    assert days_text(list(range(1, 8)), "ru") == "каждый день"


async def test_the_assistant_keeps_one_draft_per_conversation(tmp_path, monkeypatch):
    llms: list[ScriptedLLM] = []
    script = [
        [turn(text=dispatcher_reading(intent="setup").model_dump_json()), turn(text=OPEN_OLMAZOR.model_dump_json())],
        [turn(text=dispatcher_reading(intent="setup").model_dump_json()), turn(text=asked(leave_out=["Бобура"]).model_dump_json())],
    ]

    def model(self):
        if len(llms) % 2 == 0:
            llms.append(ScriptedLLM(script[len(llms) // 2]))
        else:
            llms.append(llms[-1])
        return llms[-1]

    monkeypatch.setattr(Settings, "analyst_llm", model)
    monkeypatch.setattr(Settings, "skeptic_llm", lambda self: ScriptedLLM([]))
    settings = Settings(_env_file=None, model_api_key="k", demo_mode=True, demo_now=NOW, agent_db_path=str(tmp_path / "a.sqlite3"))
    link = connect(settings)
    helper = Assistant(settings, ProposalStore(tmp_path / "a.sqlite3"), service=link.client())

    first = await helper.ask(f"Открываем ЖК Олмазор {OLMAZOR}, смена с 8 до 17 пн–сб, бригада Рустама: Рустам и Бобур Каримов", conversation="w")
    second = await helper.ask("Бобура не надо", conversation="w")

    assert first.view["reply"] == "setup" and first.view["setup"]["ready"] and first.view["cards"] == []
    assert "A setup is being made in this conversation" in llms[2].requests[0]["instructions"]
    assert second.view["setup"]["steps"][-1]["text"].endswith("Рустам Юлдашев.")
    assert helper.setup.drafts.get("w").person("Бобур Каримов").state == "skip"


def test_the_bot_shows_the_plan_and_sends_the_owner_to_the_window():
    from nazar_agent.telegram.render import answer_header

    view = {"summary": "Подготовил план", "cards": [], "setup": {
        "steps": [{"kind": "create_group", "text": "Создам группу «<b>»."}], "clashes": [], "missing": [{"text": "Где объект?"}],
    }}

    text = answer_header(view)

    assert "1. Создам группу «&lt;b&gt;»." in text and "? Где объект?" in text and "в окне помощника" in text
