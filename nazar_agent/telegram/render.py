"""Cards as Telegram messages: HTML text plus inline buttons per action.

Everything from the model or NAZAR is escaped — a name or a reason must not
be able to inject markup. A card is short: what the team will do if the owner
agrees, in a sentence written by code; the reasoning is folded in an
expandable quote. Button data is "<verb>:<proposal id>", within
Telegram's 64-byte limit.
"""

from __future__ import annotations

from html import escape
from typing import Any

MAX_MESSAGE = 4000  # Telegram's limit is 4096

CONFIDENCE = {"high": "высокая", "medium": "средняя", "low": "низкая"}
VERDICT = {"concern_supported": "повод есть", "inconclusive": "не уверен", "innocent_likely": "скорее всё в порядке"}
DAY_FIELDS = {"late_minutes": "Опоздание", "paid_minutes": "К оплате", "worked_minutes": "Отработано", "early_leave_minutes": "Ранний уход"}
WEEKDAYS = ("", "пн", "вт", "ср", "чт", "пт", "сб", "вс")
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
    ]
    if card["actions"]:
        lines += ["", "<b>Сделаю, если согласитесь:</b>"]
    for index, action in enumerate(card["actions"], start=1):
        lines.append(action_text(action, index))
    head = _fit("\n".join(lines))
    return head + _folded(_reasoning(card), MAX_MESSAGE - len(head))


def _reasoning(card: dict[str, Any]) -> list[str]:
    """Why the team thinks so: shown folded, under the actions."""
    lines = [f"✓ {escape(fact['text'])}" for fact in card["facts"]]
    lines += ["", f"<i>Возможно:</i> {escape(card['hypothesis'])}"]
    if card["alternatives"]:
        lines += ["<i>Может быть и иначе:</i>"] + [f"↔ {escape(text)}" for text in card["alternatives"]]
    if card.get("owner_said"):
        lines += ["<i>Раньше вы не согласились:</i>"] + [f"«{escape(text)}»" for text in card["owner_said"]]
    if card["check_steps"]:
        lines += ["<i>Как проверить:</i>"] + [f"→ {escape(text)}" for text in card["check_steps"]]
    return lines


def _folded(lines: list[str], room: int) -> str:
    """An expandable quote cut at a line to fit: Telegram shows its first lines and "more"."""
    opening, closing = "\n\n<blockquote expandable>", "</blockquote>"
    kept: list[str] = []
    size = len(opening) + len(closing)
    for line in lines:
        if size + len(line) + 2 > room:
            kept.append("…")
            break
        kept.append(line)
        size += len(line) + 1
    return opening + "\n".join(kept) + closing if kept and kept != ["…"] else ""


def action_text(action: dict[str, Any], index: int) -> str:
    lines = [f"<b>{index}.</b> {escape(action['text'])}"]
    preview = action.get("preview")
    if action["name"] == "change_schedule" and preview and preview.get("before") and preview.get("after"):
        lines.extend(_schedule_change(preview["before"], preview["after"]))
    elif preview and preview.get("before") and preview.get("after"):
        for field, label in DAY_FIELDS.items():
            before, after = preview["before"].get(field), preview["after"].get(field)
            if before != after:
                lines.append(f"   {label}: {before} → <b>{after}</b> мин")
    if action.get("reason"):
        lines.append(f"   <i>В NAZAR будет записано:</i> «{escape(action['reason'])}»")
    if action["level"] == "changes_pay":
        lines.append("   <i>Меняет оплату — посчитал сам NAZAR</i>")
    if action["status"] in STATUS:
        lines.append(f"   Статус: {STATUS[action['status']]}")
    return "\n".join(lines)


def _schedule_change(before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    def days(value: list[int] | None) -> str:
        return ", ".join(WEEKDAYS[day] for day in value or [])

    lines = [f"   Смена: {before.get('shift_start')}–{before.get('shift_end')} → <b>{after.get('shift_start')}–{after.get('shift_end')}</b>"]
    if before.get("working_days") != after.get("working_days"):
        lines.append(f"   Рабочие дни: {days(before.get('working_days'))} → <b>{days(after.get('working_days'))}</b>")
    return lines


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
    answer = view.get("answer", [])
    if answer:
        lines += [""] + [f"• {escape(fact['text'])}" for fact in answer]
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


ROLES = {
    "dispatcher": "диспетчер",
    "attendance_analyst": "аналитик отметок",
    "timesheet_analyst": "аналитик табеля",
    "photos": "инспектор фото",
    "skeptic": "скептик",
    "writer": "составитель",
    "voice": "голос",
    "import": "разбор списков",
}


def usage_text(summary: dict[str, Any]) -> str:
    """What the agents spent on models, for /usage."""
    def period(name: str, label: str) -> str:
        item = summary[name]
        estimate = " (часть — оценка)" if item["estimated"] else ""
        return (
            f"<b>{label}:</b> ${item['cost_usd']:.4f}{estimate} · запусков {item['runs']} · "
            f"токенов {item['input_tokens']:,} → {item['output_tokens']:,}".replace(",", " ")
        )

    lines = ["💰 <b>Расходы на модели</b>", period("today", "Сегодня"), period("week", "7 дней"), period("all", "Всего")]
    if summary["all"]["per_run_usd"] is not None:
        lines.append(f"В среднем за запуск: ${summary['all']['per_run_usd']:.4f}")
    if summary["by_role"]:
        lines.append("")
        lines += [
            f"• {ROLES.get(row['role'], escape(row['role']))} — {escape(row['model'])}: ${row['cost_usd']:.4f}"
            + (" (оценка)" if row["estimated"] else "")
            for row in summary["by_role"]
        ]
    return "\n".join(lines)

