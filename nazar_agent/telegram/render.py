"""Cards as Telegram messages: HTML text plus inline buttons per action.

Everything from the model or NAZAR is escaped — a name or a reason must not
be able to inject markup. Button data is "<verb>:<proposal id>", within
Telegram's 64-byte limit.
"""

from __future__ import annotations

from html import escape
from typing import Any

MAX_MESSAGE = 4000  # Telegram's limit is 4096

CONFIDENCE = {"high": "высокая", "medium": "средняя", "low": "низкая"}
VERDICT = {"concern_supported": "повод есть", "inconclusive": "не уверен", "innocent_likely": "скорее всё в порядке"}
ACTIONS = {
    "review_event": "Записать итог разбора отметки",
    "adjust_shift": "Исправить засчитанное время смены",
    "void_shift": "Не засчитывать день",
}
REVIEW_STATUS = {"no_violation": "нарушения нет", "confirmed": "нарушение подтверждено", "insufficient_data": "недостаточно данных"}
DAY_FIELDS = {"late_minutes": "Опоздание", "paid_minutes": "К оплате", "worked_minutes": "Отработано", "early_leave_minutes": "Ранний уход"}
STATUS = {
    "executed": "выполнено в NAZAR",
    "declined": "отклонено",
    "undone": "отменено, NAZAR вернул как было",
    "stale": "данные изменились — спросите заново",
    "failed": "не выполнено",
}


def card_text(card: dict[str, Any], number: int) -> str:
    skeptic = VERDICT[card["skeptic"]["verdict"]] if card.get("skeptic") else "не высказался"
    lines = [
        f"<b>{number}. {escape(card['title'])}</b> — {escape(card['subject']['name'])}",
        f"Уверенность: {CONFIDENCE[card['confidence']]} "
        f"(аналитик: {CONFIDENCE[card['analyst_confidence']]}, скептик: {skeptic})",
        "",
    ]
    lines += [f"✓ {escape(fact['text'])}" for fact in card["facts"]]
    lines += ["", f"<i>Возможно:</i> {escape(card['hypothesis'])}"]
    if card["alternatives"]:
        lines += ["<i>Может быть и иначе:</i>"] + [f"↔ {escape(text)}" for text in card["alternatives"]]
    if card["check_steps"]:
        lines += ["<i>Как проверить:</i>"] + [f"→ {escape(text)}" for text in card["check_steps"]]
    for index, action in enumerate(card["actions"], start=1):
        lines += ["", action_text(action, index)]
    return _fit("\n".join(lines))


def action_text(action: dict[str, Any], index: int) -> str:
    params = action["params"]
    if action["name"] == "review_event":
        what = f"итог: {REVIEW_STATUS.get(params['status'], params['status'])}"
    elif action["name"] == "adjust_shift":
        spans = ", ".join(f"{item['start_at'][11:16]}–{item['end_at'][11:16]}" for item in params["intervals"])
        what = f"{params['work_date']}: засчитать {spans}"
    else:
        what = f"{params['work_date']}: не засчитывать"
    lines = [f"<b>Действие {index}.</b> {ACTIONS.get(action['name'], action['name'])} — {escape(what)}"]
    preview = action.get("preview")
    if preview and preview.get("before") and preview.get("after"):
        for field, label in DAY_FIELDS.items():
            before, after = preview["before"].get(field), preview["after"].get(field)
            if before != after:
                lines.append(f"   {label}: {before} → <b>{after}</b> мин")
    if action["level"] == "changes_pay":
        lines.append("   <i>Меняет оплату — посчитал сам NAZAR</i>")
    if action["status"] in STATUS:
        lines.append(f"   Статус: {STATUS[action['status']]}")
    return "\n".join(lines)


def card_keyboard(card: dict[str, Any]) -> list[list[dict[str, str]]]:
    rows = []
    for index, action in enumerate(card["actions"], start=1):
        proposal = action["proposal_id"]
        if action["status"] == "pending":
            rows.append([
                _button(f"✅ Применить {index}", f"a:{proposal}"),
                _button("✗ Не согласен", f"d:{proposal}"),
                _button("⏸ Позже", f"p:{proposal}"),
            ])
        elif action["status"] == "executed":
            rows.append([_button(f"↩ Отменить {index}", f"u:{proposal}")])
    return rows


def confirm_keyboard(proposal: str, index: int) -> list[list[dict[str, str]]]:
    return [[_button(f"✅ Да, изменить оплату ({index})", f"y:{proposal}"), _button("← Назад", f"b:{proposal}")]]


def answer_header(view: dict[str, Any]) -> str:
    lines = [escape(view["summary"])]
    gaps = view.get("data_gaps", []) + view.get("notes", [])
    if gaps:
        lines += ["", "<i>Чего не видно в данных:</i>"] + [f"• {escape(text)}" for text in gaps]
    if not view["cards"]:
        lines += ["", "Проверять нечего."]
    return _fit("\n".join(lines))


def answer_footer(view: dict[str, Any]) -> str:
    rounds = sum(len(round_) for round_ in view.get("fact_checker_rounds", []))
    removed = len(view.get("removed_by_fact_checker", []))
    stats = view["stats"]
    return (
        f"<i>Проверка фактов: исправлено {rounds}, удалено {removed}. "
        f"Запросов к NAZAR: {stats['tool_calls']}. Стоимость ~${stats['cost_usd']}.</i>"
    )


def _button(text: str, data: str) -> dict[str, str]:
    return {"text": text, "callback_data": data}


def _fit(text: str) -> str:
    """Cut at a line break: every tag opens and closes on one line, so none is cut in half."""
    if len(text) <= MAX_MESSAGE:
        return text
    return text[: text.rfind("\n", 0, MAX_MESSAGE - 2)] + "\n…"
