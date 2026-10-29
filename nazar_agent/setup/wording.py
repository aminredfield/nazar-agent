"""The plan of changes in the owner's words — each step one sentence, written by code.

Like the actions' sentences (``recommendations.wording``): built from the
very parameters that will be sent to NAZAR, so a step cannot say one thing
and do another. Russian, Uzbek and English; each step reads the same in the
plan and in the list of what was done ("Create…", not "I will create…").
"""

from __future__ import annotations

from datetime import date
from typing import Any

from nazar_agent.recommendations.wording import WEEKDAYS, Language, language
from nazar_agent.setup.plan import PersonClash, Plan, Step

EVERY_DAY = {"ru": "каждый день", "uz": "har kuni", "en": "every day"}
TEXT: dict[Language, dict[str, str]] = {
    "ru": {
        "create_site": "Создать объект «{name}»{where}: точка {lat}, {lng}, радиус {radius} м; смена {start}–{end}, {days}.",
        "site_schedule": "Поставить объекту «{site}» смену {start}–{end}, {days}, с сегодняшнего дня.",
        "create_group": "Создать группу «{name}».",
        "group_on_site": "Поставить группу «{group}» на объект «{site}» — по графику объекта.",
        "group_on_site_custom": "Поставить группу «{group}» на объект «{site}» со своей сменой {start}–{end}, {days}.",
        "add_to_group": "Добавить в группу «{group}»: {names}.",
        "assign": "Назначить на объект «{site}» с {date}: {names}.",
        "transfer": "Перевести {name} с объекта «{from_site}» на «{site}» с {date}. Отменить перевод можно только в NAZAR вручную.",
        "wait_for_photo": "Добавить в «Ждут фото»: {names} — NAZAR создаст каждого после фото лица.",
        "missing_site_name": "Как назвать объект?",
        "missing_point": "Где объект? Поставьте точку на карте, вставьте ссылку Яндекс или Google Карт или нажмите «Я здесь».",
        "missing_shift": "Какая смена на объекте: с какого до какого часа и в какие дни?",
        "missing_site": "На какой объект ставим людей?",
        "missing_choose_person": "«{name}» — несколько похожих сотрудников; выберите нужного.",
        "missing_nothing": "Что настроить? Например: «Открываем объект ЖК Олмазор на Чиланзаре, смена с 8 до 17, бригада Рустама».",
        "clash": "{name} уже работает на объекте «{site}» {start}–{end} — смены пересекаются по {days} ({window}).",
        "unknown_schedule": "У {names} на другом объекте не недельный график — пересечения проверьте вручную.",
        "link_not_a_map": "Это не ссылка на карту — точку из неё не взять.",
        "link_no_point": "В ссылке нет точки — откройте место на карте и скопируйте ссылку на него.",
        "link_unreachable": "Ссылка не открылась — поставьте точку на карте или нажмите «Я здесь».",
        "summary_ready": "Подготовил план: шагов — {count}. Проверьте карточки и нажмите «Применить всё».",
        "summary_missing": "Готовлю план. Чтобы его применить, ответьте на вопросы ниже.",
    },
    "uz": {
        "create_site": "«{name}» obyektini yaratish{where}: nuqta {lat}, {lng}, radius {radius} m; smena {start}–{end}, {days}.",
        "site_schedule": "«{site}» obyektiga bugundan boshlab {start}–{end}, {days} smenasini qo'yish.",
        "create_group": "«{name}» guruhini yaratish.",
        "group_on_site": "«{group}» guruhini «{site}» obyektiga qo'yish — obyekt jadvali bo'yicha.",
        "group_on_site_custom": "«{group}» guruhini «{site}» obyektiga o'z smenasi bilan qo'yish: {start}–{end}, {days}.",
        "add_to_group": "«{group}» guruhiga qo'shish: {names}.",
        "assign": "{date} dan «{site}» obyektiga tayinlash: {names}.",
        "transfer": "{name}ni {date} dan «{from_site}» obyektidan «{site}» ga o'tkazish. O'tkazishni faqat NAZAR'da qo'lda bekor qilish mumkin.",
        "wait_for_photo": "«Surat kutmoqda» ga qo'shish: {names} — NAZAR har birini yuz suratidan keyin yaratadi.",
        "missing_site_name": "Obyekt qanday nomlansin?",
        "missing_point": "Obyekt qayerda? Xaritada nuqta qo'ying, Yandex yoki Google xarita havolasini qo'ying yoki «Men shu yerdaman» ni bosing.",
        "missing_shift": "Obyektda smena qanday: soat nechadan nechagacha va qaysi kunlari?",
        "missing_site": "Odamlarni qaysi obyektga qo'yamiz?",
        "missing_choose_person": "«{name}» — bir nechta o'xshash xodim bor; keraklisini tanlang.",
        "missing_nothing": "Nimani sozlaymiz? Masalan: «Chilonzorda ЖК Olmazor obyektini ochamiz, smena 8 dan 17 gacha, Rustam brigadasi».",
        "clash": "{name} allaqachon «{site}» obyektida {start}–{end} ishlaydi — smenalar {days} kunlari kesishadi ({window}).",
        "unknown_schedule": "{names}ning boshqa obyektdagi jadvali haftalik emas — kesishishlarni qo'lda tekshiring.",
        "link_not_a_map": "Bu xarita havolasi emas — undan nuqtani olib bo'lmaydi.",
        "link_no_point": "Havolada nuqta yo'q — joyni xaritada oching va unga havolani nusxalang.",
        "link_unreachable": "Havola ochilmadi — xaritada nuqta qo'ying yoki «Men shu yerdaman» ni bosing.",
        "summary_ready": "Reja tayyor: {count} ta qadam. Kartochkalarni tekshiring va «Hammasini qo'llash» ni bosing.",
        "summary_missing": "Reja tayyorlanmoqda. Uni qo'llash uchun quyidagi savollarga javob bering.",
    },
    "en": {
        "create_site": "Create the location “{name}”{where}: point {lat}, {lng}, radius {radius} m; shift {start}–{end}, {days}.",
        "site_schedule": "Set the location “{site}” to a {start}–{end} shift, {days}, from today.",
        "create_group": "Create the group “{name}”.",
        "group_on_site": "Put the group “{group}” on the location “{site}”, on the location's schedule.",
        "group_on_site_custom": "Put the group “{group}” on the location “{site}” with its own shift {start}–{end}, {days}.",
        "add_to_group": "Add to the group “{group}”: {names}.",
        "assign": "Assign to the location “{site}” from {date}: {names}.",
        "transfer": "Move {name} from “{from_site}” to “{site}” from {date}. A move can only be undone by hand in NAZAR.",
        "wait_for_photo": "Add to “Waiting for a photo”: {names} — NAZAR creates each one after a face photo.",
        "missing_site_name": "What is the location called?",
        "missing_point": "Where is the location? Put a point on the map, paste a Yandex or Google Maps link, or press “I am here”.",
        "missing_shift": "What is the shift there: from what time to what time, and on which days?",
        "missing_site": "Which location do the people go to?",
        "missing_choose_person": "“{name}” — several employees fit; choose the right one.",
        "missing_nothing": "What shall we set up? For example: “We open the Olmazor site in Chilanzar, shift 8 to 17, Rustam's crew”.",
        "clash": "{name} already works at “{site}” {start}–{end} — the shifts overlap on {days} ({window}).",
        "unknown_schedule": "{names} has a schedule elsewhere that is not weekly — check overlaps by hand.",
        "link_not_a_map": "This is not a map link — there is no point to take from it.",
        "link_no_point": "The link has no point — open the place on the map and copy the link to it.",
        "link_unreachable": "The link did not open — put a point on the map or press “I am here”.",
        "summary_ready": "The plan is ready: {count} steps. Check the cards and press “Apply all”.",
        "summary_missing": "Preparing the plan. To apply it, answer the questions below.",
    },
}


def step_text(step: Step, plan: Plan, lang: str) -> str:
    words, lang_ = TEXT[language(lang)], language(lang)
    params = step.params
    site = plan.site_name
    shared = {
        "site": site, "group": plan.group_name, "names": ", ".join(params.get("names", [])),
        "date": _day(params.get("start_date") or params.get("from_date") or ""),
    }
    if "shift_start" in params:
        shared |= {"start": params["shift_start"], "end": params["shift_end"], "days": days_text(params["working_days"], lang_)}
    if step.kind == "create_site":
        address = (params.get("address") or "").strip()
        # NAZAR needs an address; without one the plan sends the name, and saying it twice says nothing.
        where = "" if not address or address.casefold() == params["name"].strip().casefold() else f", {address}"
        return words["create_site"].format(
            **shared, name=params["name"], where=where, lat=f"{params['latitude']:.5f}",
            lng=f"{params['longitude']:.5f}", radius=params["radius_meters"],
        )
    if step.kind == "create_group":
        return words["create_group"].format(name=params["name"])
    if step.kind == "group_on_site":
        return words["group_on_site_custom" if params["schedule_mode"] == "custom" else "group_on_site"].format(**shared)
    if step.kind == "transfer":
        return words["transfer"].format(**shared, name=params["name"], from_site=params["from_name"])
    return words[step.kind].format(**shared)


def missing_text(kind: str, about: str, lang: str) -> str:
    return TEXT[language(lang)][f"missing_{kind}"].format(name=about)


def clash_text(item: PersonClash, lang: str) -> str:
    lang_ = language(lang)
    return TEXT[lang_]["clash"].format(
        name=item.person.name, site=item.site_name, start=item.theirs.start, end=item.theirs.end,
        days=days_text(list(item.clash.weekdays), lang_), window="–".join(item.clash.window),
    )


def text(key: str, lang: str, **values: Any) -> str:
    return TEXT[language(lang)][key].format(**values)


def days_text(days: list[int], lang: Language) -> str:
    """1..6 → "пн–сб"; 1, 3, 5 → "пн, ср, пт"; every day in words."""
    days = sorted(set(days))
    if len(days) == 7:
        return EVERY_DAY[lang]
    names = WEEKDAYS[lang]
    runs: list[list[int]] = []
    for day in days:
        if runs and day == runs[-1][-1] + 1:
            runs[-1].append(day)
        else:
            runs.append([day])
    return ", ".join(
        f"{names[run[0] - 1]}–{names[run[-1] - 1]}" if len(run) > 2 else ", ".join(names[day - 1] for day in run) for run in runs
    )


def _day(value: str) -> str:
    try:
        return date.fromisoformat(value).strftime("%d.%m")
    except ValueError:
        return value
