"""What an action will do, in the owner's words — written by code, not by a model.

The sentence names the person or the site, the day and the hours, and says
before → after. It is built from the very parameters that will be sent to
NAZAR and from NAZAR's own preview, so it cannot promise one thing while the
button does another. Names come from what the tools returned in the run.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Literal

from nazar_agent.findings import Evidence

Language = Literal["ru", "en", "uz"]
TargetKind = Literal["employee", "site", "event"]
TARGET_KIND: dict[str, TargetKind] = {
    "review_event": "event",
    "adjust_shift": "employee",
    "void_shift": "employee",
    "change_schedule": "site",
}
WEEKDAYS: dict[Language, tuple[str, ...]] = {
    "ru": ("пн", "вт", "ср", "чт", "пт", "сб", "вс"),
    "en": ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"),
    "uz": ("Du", "Se", "Ch", "Pa", "Ju", "Sh", "Ya"),
}
REVIEW_STATUS: dict[Language, dict[str, str]] = {
    "ru": {"no_violation": "нарушения нет", "confirmed": "нарушение подтверждено", "insufficient_data": "недостаточно данных"},
    "en": {"no_violation": "no violation", "confirmed": "violation confirmed", "insufficient_data": "not enough data"},
    "uz": {"no_violation": "qoidabuzarlik yo'q", "confirmed": "qoidabuzarlik tasdiqlandi", "insufficient_data": "ma'lumot yetarli emas"},
}
EVENT_KIND: dict[Language, dict[str, str]] = {
    "ru": {"check_in": "приход", "check_out": "уход"},
    "en": {"check_in": "check-in", "check_out": "check-out"},
    "uz": {"check_in": "kelish", "check_out": "ketish"},
}
HOURS: dict[Language, tuple[str, str]] = {"ru": ("ч", "мин"), "en": ("h", "min"), "uz": ("soat", "daqiqa")}
TEXT: dict[Language, dict[str, str]] = {
    "ru": {
        "schedule": "Объект{site}: смена {after}{before} с сегодняшнего дня{days}. Прошлые дни NAZAR не пересчитает.",
        "schedule_before": " вместо {before}",
        "schedule_days": ", рабочие дни {after} вместо {before}",
        "adjust": "{who}{day}: засчитаю {spans}.",
        "void": "{who}{day}: день не засчитаю.",
        "paid": " К оплате: {before} → {after}.",
        "review": "{who}{kind} {day}{site}: запишу итог разбора «{status}». Оплата не меняется.",
    },
    "en": {
        "schedule": "Location{site}: shift {after}{before} from today{days}. NAZAR does not recount past days.",
        "schedule_before": " instead of {before}",
        "schedule_days": ", working days {after} instead of {before}",
        "adjust": "{who}{day}: count {spans}.",
        "void": "{who}{day}: the day will not be counted.",
        "paid": " Paid: {before} → {after}.",
        "review": "{who}{kind} {day}{site}: record the review result “{status}”. Pay does not change.",
    },
    "uz": {
        "schedule": "Obyekt{site}: bugundan boshlab smena {after}{before}{days}. O'tgan kunlarni NAZAR qayta hisoblamaydi.",
        "schedule_before": " ({before} o'rniga)",
        "schedule_days": ", ish kunlari {after} ({before} o'rniga)",
        "adjust": "{who}{day}: {spans} hisoblanadi.",
        "void": "{who}{day}: kun hisoblanmaydi.",
        "paid": " To'lovga: {before} → {after}.",
        "review": "{who}{kind} {day}{site}: tekshiruv natijasi «{status}» deb yoziladi. To'lov o'zgarmaydi.",
    },
}


def language(lang: str) -> Language:
    """The request's language as one of the three the texts are written in; Uzbek Cyrillic reads the Latin ones."""
    if lang.startswith("uz"):
        return "uz"
    return "en" if lang == "en" else "ru"


def about(action: str, target: str, evidence: Evidence) -> dict[str, str]:
    """Who or what an action touches, as the tools of this run named it."""
    kind = TARGET_KIND.get(action)
    record = evidence.records.get(target, {})
    if kind == "event":
        found = {
            "name": record.get("employee_name"),
            "site": record.get("object_name"),
            "at": record.get("at"),
            "kind": record.get("type"),
        }
    elif kind == "employee":
        found = {"name": record.get("full_name") or record.get("employee_name") or record.get("name")}
        found["name"] = found["name"] or _named_by(evidence, "employee_id", target, "employee_name")
    elif kind == "site":
        found = {"site": record.get("object_name") or record.get("name") or _named_by(evidence, "object_id", target, "object_name")}
    else:
        found = {}
    return {key: str(value) for key, value in found.items() if value}


def describe(action: str, params: dict[str, Any], preview: dict[str, Any] | None, card: dict[str, Any], lang: str) -> str:
    """One sentence: what will change in NAZAR if the owner presses "Apply"."""
    words = TEXT[language(lang)]
    named = _with_subject(action, params, card)
    if action == "change_schedule":
        return _schedule(params, preview, named, language(lang))
    if action in ("adjust_shift", "void_shift"):
        who = f"{named['name']}, " if named.get("name") else ""
        day = _day(params["work_date"])
        if action == "adjust_shift":
            spans = ", ".join(f"{item['start_at'][11:16]}–{item['end_at'][11:16]}" for item in params["intervals"])
            text = words["adjust"].format(who=who, day=day, spans=spans)
        else:
            text = words["void"].format(who=who, day=day)
        return text + _paid(preview, language(lang))
    if action == "review_event":
        lang_ = language(lang)
        at = named.get("at", "")
        return words["review"].format(
            who=f"{named['name']}, " if named.get("name") else "",
            kind=EVENT_KIND[lang_].get(named.get("kind", ""), EVENT_KIND[lang_]["check_in"]),
            day=f"{_day(at[:10])} {at[11:16]}" if at else "",
            site=f", {named['site']}" if named.get("site") else "",
            status=REVIEW_STATUS[lang_].get(params["status"], params["status"]),
        )
    return action


def _schedule(params: dict[str, Any], preview: dict[str, Any] | None, named: dict[str, str], lang: Language) -> str:
    words = TEXT[lang]
    before = (preview or {}).get("before") or {}
    after = (preview or {}).get("after") or {}
    shift_before = f"{before.get('shift_start')}–{before.get('shift_end')}" if before.get("shift_start") else ""
    days = ""
    if before.get("working_days") and after.get("working_days") and before["working_days"] != after["working_days"]:
        days = words["schedule_days"].format(after=_weekdays(after["working_days"], lang), before=_weekdays(before["working_days"], lang))
    return words["schedule"].format(
        site=f" {named['site']}" if named.get("site") else "",
        after=f"{params['shift_start']}–{params['shift_end']}",
        before=words["schedule_before"].format(before=shift_before) if shift_before else "",
        days=days,
    )


def _paid(preview: dict[str, Any] | None, lang: Language) -> str:
    before = ((preview or {}).get("before") or {}).get("paid_minutes")
    after = ((preview or {}).get("after") or {}).get("paid_minutes")
    if before is None or after is None:
        return ""
    return TEXT[lang]["paid"].format(before=duration(before, lang), after=duration(after, lang))


def duration(minutes: int, lang: Language = "ru") -> str:
    """480 → "8 ч", 545 → "9 ч 5 мин", 40 → "40 мин"."""
    hour, minute = HOURS[lang]
    hours, rest = divmod(int(minutes), 60)
    if hours and rest:
        return f"{hours} {hour} {rest} {minute}"
    return f"{hours} {hour}" if hours else f"{rest} {minute}"


def _day(value: str) -> str:
    """2026-10-23 → 23.10."""
    try:
        return date.fromisoformat(value).strftime("%d.%m")
    except ValueError:
        return value


def _weekdays(days: list[int], lang: Language) -> str:
    return ", ".join(WEEKDAYS[lang][day - 1] for day in days if 1 <= day <= 7)


def _with_subject(action: str, params: dict[str, Any], card: dict[str, Any]) -> dict[str, str]:
    """The names found in the run; failing that, the card's own subject when it is the same record."""
    named = dict(card.get("about") or {})
    subject = card.get("subject") or {}
    target = params.get("object_id") or params.get("employee_id")
    if target and subject.get("id") == target and subject.get("name"):
        key = "site" if TARGET_KIND.get(action) == "site" else "name"
        named.setdefault(key, subject["name"])
    return named


def _named_by(evidence: Evidence, key: str, target: str, name_key: str) -> str | None:
    return next(
        (record[name_key] for record in evidence.records.values() if record.get(key) == target and record.get(name_key)),
        None,
    )

