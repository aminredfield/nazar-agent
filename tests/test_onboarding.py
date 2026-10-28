import io
import json

import pytest
from openpyxl import Workbook

from nazar_agent.actions import ProposalStore
from nazar_agent.engine import ToolExecutionError
from nazar_agent.onboarding import DraftStore, ListedPerson, ParseError, PhotoRejected, Site, read_list, review
from nazar_agent.onboarding.parse import PEOPLE_FORMAT, sheet_text
from nazar_agent.onboarding.review import match_site, same_person
from tests import factories
from tests.fakes import ScriptedLLM, turn

SITES = [Site(item["id"], item["name"], item["address"]) for item in factories.SITES]


def person(name, site="", legible=True, note="", written=None):
    return ListedPerson(full_name=name, site=site, as_written=written or name, legible=legible, note=note)


# ---- reading -----------------------------------------------------------------

def people_json(*names):
    return json.dumps({"people": [{"full_name": n, "site": "", "as_written": n, "legible": True, "note": ""} for n in names]})


async def test_photo_goes_to_the_model_as_an_image():
    llm = ScriptedLLM([turn(text=people_json("Иванов Алишер"))])

    people = await read_list(llm, "spark", file=b"\xff\xd8jpeg", content_type="image/jpeg")

    assert [p.full_name for p in people] == ["Иванов Алишер"]
    content = llm.requests[0]["input"][0]["content"]
    assert content[1]["type"] == "input_image" and content[1]["image_url"].startswith("data:image/jpeg;base64,")
    assert llm.requests[0]["text_format"] == PEOPLE_FORMAT


async def test_excel_and_csv_become_text():
    workbook = Workbook()
    sheet = workbook.active
    sheet.append(["ФИО", "Телефон", "Объект"])
    sheet.append(["Иванов Алишер", "+998901234567", "Чиланзар"])
    buffer = io.BytesIO()
    workbook.save(buffer)

    text = sheet_text(buffer.getvalue())
    llm = ScriptedLLM([turn(text=people_json())])
    await read_list(llm, "spark", file="ФИО;Объект\nСидоров;Юнусабад\n".encode("cp1251"), content_type="text/csv")

    assert "Иванов Алишер | +998901234567 | Чиланзар" in text
    assert "Сидоров | Юнусабад" in llm.requests[0]["input"][0]["content"][1]["text"]


async def test_unreadable_inputs_are_refused_in_plain_words():
    with pytest.raises(ParseError, match="Пришлите"):
        await read_list(ScriptedLLM([]), "spark", text="  ")
    with pytest.raises(ParseError, match="формат"):
        await read_list(ScriptedLLM([]), "spark", file=b"%PDF", content_type="application/pdf")
    with pytest.raises(ParseError, match="Excel"):
        sheet_text(b"not a workbook")
    with pytest.raises(ParseError, match="разобрать"):
        await read_list(ScriptedLLM([turn(text="{oops")]), "spark", text="Иванов")


# ---- checks ------------------------------------------------------------------

def test_rows_that_need_a_decision_are_highlighted_with_the_reason():
    rows = review(
        [
            person("Иванов Алишер", site="Чиланзар"),
            person("Хакимова Зарина"),  # existing employee, other word order
            person("Сидоров"),
            person("Абдулаев Рустам", legible=False, note="вторая буква неразборчива", written="Абд?лаев Р."),
            person("Иванов  Алишер", site="Объект 3"),
        ],
        SITES,
        factories.EMPLOYEES,
    )

    ivanov, zarina, sidorov, rustam, twin = rows
    assert ivanov.issues == () and ivanov.site_id == factories.SITE
    assert "похож на действующего сотрудника «Зарина Хакимова»" in zarina.issues[0]
    assert sidorov.issues == ("только фамилия — допишите имя или оставьте так",)
    assert rustam.issues[0] == "плохо читается: «Абд?лаев Р.» — вторая буква неразборчива"
    assert twin.full_name == "Иванов Алишер"
    assert set(twin.issues) == {"встречается в списке дважды", "объект «Объект 3» не найден — выберите из списка"}
    assert twin.site_id is None


def test_name_and_site_matching():
    assert same_person("Ёлкин Пётр", "елкин петр")
    assert not same_person("Иванов Алишер", "Иванова Алия")
    assert match_site("юнусабад", SITES).name == "Магазин Юнусабад"
    assert match_site("9 квартал", SITES).name == "Магазин Чиланзар"  # by address
    assert match_site("Магазин", SITES) is None  # fits both: do not guess
    assert match_site("Объект Юнусабад", SITES).name == "Магазин Юнусабад"  # "объект" names no site
    assert match_site("Объект", SITES) is None


# ---- drafts and creating employees ----------------------------------------------

@pytest.fixture
def drafts(tmp_path):
    audit = ProposalStore(tmp_path / "agent.sqlite3")
    return DraftStore(tmp_path / "agent.sqlite3", audit), audit


def add(store, site=factories.SITE):
    [draft] = store.add_many([("Иванов Алишер", site, "Магазин Чиланзар")], None, "Шерзод")
    return draft


async def test_photo_creates_the_employee_and_keeps_everyone_on_the_site(drafts):
    store, audit = drafts
    nazar, seen = factories.nazar()
    draft = add(store)

    created = await store.photograph(draft.id, b"jpeg", "image/jpeg", nazar, "Шерзод")

    assert created.status == "created" and created.employee_id == factories.NEW_EMPLOYEE
    paths = [(r.method, r.url.path.removeprefix("/api")) for r in seen if "login" not in r.url.path]
    assert paths == [
        ("POST", "/face/quality-check"),
        ("POST", "/employees"),
        ("GET", f"/objects/{factories.SITE}/assignments"),
        ("POST", f"/objects/{factories.SITE}/assignments"),
    ]
    employee_form = next(r for r in seen if r.url.path.endswith("/employees") and r.method == "POST")
    assert b'name="full_name"' in employee_form.content and "Иванов Алишер".encode() in employee_form.content
    assignment = json.loads(next(r for r in seen if r.method == "POST" and r.url.path.endswith("/assignments")).content)
    assert assignment["employee_ids"] == [factories.EMPLOYEE, factories.NEW_EMPLOYEE]
    assert [entry.event for entry in audit.audit(proposal_id=f"draft:{draft.id}")] == ["employee_created", "draft_created"]
    assert store.waiting() == []


async def test_bad_photo_is_refused_with_nazars_reason_and_the_person_keeps_waiting(drafts, monkeypatch):
    store, audit = drafts
    monkeypatch.setattr(factories, "PHOTO_CHECK", {"face_detected": True, "blocking_reasons": ["BLURRY_IMAGE: Фото слишком размытое."]})
    nazar, seen = factories.nazar()
    draft = add(store)

    with pytest.raises(PhotoRejected) as rejected:
        await store.photograph(draft.id, b"jpeg", "image/jpeg", nazar, "Шерзод")

    assert rejected.value.reasons == ["Фото слишком размытое."]
    assert store.get(draft.id).status == "waiting"
    assert not any(r.url.path.endswith("/employees") for r in seen)
    assert audit.audit(proposal_id=f"draft:{draft.id}")[0].event == "photo_rejected"


async def test_no_face_is_refused(drafts, monkeypatch):
    store, _ = drafts
    monkeypatch.setattr(factories, "PHOTO_CHECK", {"face_detected": False, "blocking_reasons": []})
    nazar, _ = factories.nazar()

    with pytest.raises(PhotoRejected, match="Лицо не найдено"):
        await store.photograph(add(store).id, b"jpeg", "image/jpeg", nazar, "Шерзод")


async def test_a_person_is_created_once(drafts):
    store, _ = drafts
    nazar, _ = factories.nazar()
    draft = add(store, site=None)
    await store.photograph(draft.id, b"jpeg", "image/jpeg", nazar, "Шерзод")

    with pytest.raises(ToolExecutionError, match="already created"):
        await store.photograph(draft.id, b"jpeg", "image/jpeg", nazar, "Шерзод")


async def test_nazar_failure_leaves_the_person_waiting(drafts):
    store, _ = drafts
    nazar, _ = factories.nazar(write_status=500)
    draft = add(store)

    with pytest.raises(ToolExecutionError):
        await store.photograph(draft.id, b"jpeg", "image/jpeg", nazar, "Шерзод")

    assert store.get(draft.id).status == "waiting"


def test_discard_only_while_waiting(drafts):
    store, audit = drafts
    draft = add(store)

    assert store.discard(draft.id, "Шерзод") is True
    assert store.discard(draft.id, "Шерзод") is False
    assert audit.audit(proposal_id=f"draft:{draft.id}")[0].event == "draft_discarded"


async def test_photo_without_a_face_gets_nazars_own_words(drafts, monkeypatch):
    store, _ = drafts
    monkeypatch.setattr(factories, "PHOTO_CHECK", None)
    nazar, _ = factories.nazar()
    draft = add(store)

    with pytest.raises(PhotoRejected) as rejected:
        await store.photograph(draft.id, b"png", "image/png", nazar, "Шерзод")

    assert rejected.value.reasons == ["Лицо не найдено на изображении."]
    assert store.get(draft.id).status == "waiting"
