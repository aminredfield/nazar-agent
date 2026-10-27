"""The agent in Telegram: ask by text or voice, decide with buttons.

Who may use the bot is an allowlist of chat ids — a stranger who finds it
gets their chat id back and nothing else. Every decision goes through the
same executor and audit trail as the window; the Telegram user is named as
the person who decided.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass, field
from html import escape
from typing import Any, Protocol

import httpx

from nazar_agent.actions import ExecutionError
from nazar_agent.assistant import Assistant, AssistantBusy
from nazar_agent.telegram.api import TelegramApi, TelegramError
from nazar_agent.telegram.render import answer_footer, answer_header, card_keyboard, card_text, confirm_keyboard
from nazar_agent.views import proposal_view
from nazar_agent.voice import VoiceError

log = logging.getLogger(__name__)
TYPING_EVERY_SECONDS = 4  # Telegram shows "typing…" for about five seconds
BRIEF_QUESTION = (
    "Утренняя сводка: что вчера стоит проверить — невыходы, опоздания, "
    "подозрительные отметки, что-то неверно посчитанное в табеле?"
)
HELLO = (
    "Я помощник NAZAR. Спросите текстом или голосом, например:\n"
    "• «Что в табеле за прошлую неделю стоит проверить перед выплатой?»\n"
    "• «Были ли вчера подозрительные отметки?»\n\n"
    "Я покажу, что видно в данных, что может быть иначе и что можно сделать. "
    "Ничего не меняю без вашей кнопки. /brief — сводка прямо сейчас."
)


class Transcriber(Protocol):
    async def __call__(self, audio: bytes) -> str: ...


@dataclass
class _CardMessage:
    chat_id: int
    message_id: int
    number: int
    card: dict[str, Any]


@dataclass
class Bot:
    api: TelegramApi
    assistant: Assistant
    allowed_chats: frozenset[int]
    transcriber: Transcriber
    _cards: dict[str, _CardMessage] = field(default_factory=dict)  # by proposal id
    _awaiting_reason: dict[int, str] = field(default_factory=dict)  # chat id → proposal id
    _postponed: set[str] = field(default_factory=set)
    _tasks: set[asyncio.Task[None]] = field(default_factory=set)

    async def run(self) -> None:
        offset: int | None = None
        while True:
            try:
                updates = await self.api.get_updates(offset)
            except (TelegramError, httpx.HTTPError, OSError) as exc:
                # A blocked or flaky network must not end the bot: retry.
                log.warning("telegram polling failed: %s", exc)
                await asyncio.sleep(5)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                # An answer takes minutes; buttons pressed meanwhile must still work.
                task = asyncio.create_task(self.handle(update))
                self._tasks.add(task)
                task.add_done_callback(self._tasks.discard)

    async def handle(self, update: dict[str, Any]) -> None:
        try:
            if "callback_query" in update:
                await self._on_button(update["callback_query"])
            elif "message" in update:
                await self._on_message(update["message"])
        except Exception:  # one bad update must not stop the bot
            log.exception("telegram update %s failed", update.get("update_id"))

    async def brief(self, chat_ids: list[int]) -> None:
        await self._answer(chat_ids, BRIEF_QUESTION, title="☀️ <b>Утренняя сводка</b>", analyst="timesheet")

    async def _on_message(self, message: dict[str, Any]) -> None:
        chat_id = message["chat"]["id"]
        if chat_id not in self.allowed_chats:
            await self.api.send_message(
                chat_id, f"Нет доступа. Ваш chat id: <code>{chat_id}</code> — передайте его администратору NAZAR."
            )
            return

        text = (message.get("text") or "").strip()
        if text.startswith("/start") or text.startswith("/help"):
            await self.api.send_message(chat_id, escape(HELLO))
            return
        if text.startswith("/brief"):
            await self.brief([chat_id])
            return
        if chat_id in self._awaiting_reason and text:
            await self._decline(chat_id, self._awaiting_reason.pop(chat_id), _actor(message), text)
            return

        voice = message.get("voice") or message.get("audio")
        if voice:
            try:
                text = await self.transcriber(await self.api.download(voice["file_id"]))
            except VoiceError as exc:
                await self.api.send_message(chat_id, escape(str(exc)))
                return
            await self.api.send_message(chat_id, f"🎙 <i>{escape(text)}</i>")
        if text:
            await self._answer([chat_id], text)

    async def _answer(self, chat_ids: list[int], question: str, *, title: str | None = None, analyst: Any = None) -> None:
        if self.assistant.busy:
            for chat_id in chat_ids:
                await self.api.send_message(chat_id, "Я ещё отвечаю на предыдущий вопрос — пришлю, как закончу, и потом спросите снова.")
            return
        for chat_id in chat_ids:
            await self.api.send_message(
                chat_id, (f"{title}\n" if title else "") + "Смотрю данные NAZAR: аналитик, проверка фактов, скептик… обычно 1–3 минуты."
            )

        typing = asyncio.create_task(self._keep_typing(chat_ids))
        try:
            answer = await self.assistant.ask(question, analyst)
        except AssistantBusy:
            return
        except Exception:
            log.exception("answering %r failed", question)
            for chat_id in chat_ids:
                await self.api.send_message(chat_id, "Не получилось ответить — попробуйте ещё раз чуть позже.")
            return
        finally:
            typing.cancel()

        view = answer.view
        for chat_id in chat_ids:
            await self.api.send_message(chat_id, answer_header(view))
            for number, card in enumerate(view["cards"], start=1):
                sent = await self.api.send_message(chat_id, card_text(card, number), card_keyboard(card))
                for action in card["actions"]:
                    self._cards[action["proposal_id"]] = _CardMessage(chat_id, sent["message_id"], number, card)
            await self.api.send_message(chat_id, answer_footer(view))

    async def _keep_typing(self, chat_ids: list[int]) -> None:
        with contextlib.suppress(asyncio.CancelledError, TelegramError, httpx.HTTPError):
            while True:
                for chat_id in chat_ids:
                    await self.api.send_chat_action(chat_id)
                await asyncio.sleep(TYPING_EVERY_SECONDS)

    async def _on_button(self, callback: dict[str, Any]) -> None:
        chat_id = callback["message"]["chat"]["id"]
        if chat_id not in self.allowed_chats:
            await self.api.answer_callback(callback["id"], "Нет доступа.")
            return
        verb, _, proposal_id = (callback.get("data") or "").partition(":")
        entry = self._cards.get(proposal_id)
        if entry is None:
            await self.api.answer_callback(callback["id"], "Это предложение устарело — спросите заново.")
            return
        actor = _actor(callback)
        index = next(i for i, action in enumerate(entry.card["actions"], start=1) if action["proposal_id"] == proposal_id)
        action = entry.card["actions"][index - 1]

        if verb == "a" and action["level"] == "changes_pay":
            await self.api.edit_keyboard(entry.chat_id, entry.message_id, confirm_keyboard(proposal_id, index))
            await self.api.answer_callback(callback["id"], "Это изменит оплату в NAZAR — подтвердите.")
        elif verb in {"a", "y"}:
            await self._decide(callback, entry, self.assistant.executor.confirm(proposal_id, actor), "Выполнено в NAZAR.")
        elif verb == "u":
            await self._decide(callback, entry, self.assistant.executor.undo(proposal_id, actor), "Отменено, NAZAR вернул как было.")
        elif verb == "d":
            self._awaiting_reason[chat_id] = proposal_id
            await self.api.answer_callback(callback["id"])
            await self.api.send_message(chat_id, "Напишите одним сообщением, почему не согласны — агент учтёт это дальше.")
        elif verb == "p":
            self._postponed.add(proposal_id)
            await self._refresh(entry)
            await self.api.answer_callback(callback["id"], "Отложено — предложение сохранено.")
        else:  # "b": back from the pay confirmation
            await self._refresh(entry)
            await self.api.answer_callback(callback["id"])

    async def _decide(self, callback: dict[str, Any], entry: _CardMessage, outcome: Any, done: str) -> None:
        try:
            await outcome
        except ExecutionError as exc:
            await self.api.answer_callback(callback["id"], exc.message[:190])
            await self._refresh(entry)
            return
        await self._refresh(entry)
        await self.api.answer_callback(callback["id"], done)

    async def _decline(self, chat_id: int, proposal_id: str, actor: str, reason: str) -> None:
        try:
            self.assistant.executor.decline(proposal_id, actor, reason)
        except ExecutionError as exc:
            await self.api.send_message(chat_id, escape(exc.message))
            return
        if proposal_id in self._cards:
            await self._refresh(self._cards[proposal_id])
        await self.api.send_message(chat_id, "Записал. Учту в следующих рекомендациях.")

    async def _refresh(self, entry: _CardMessage) -> None:
        """Redraw a card with each action's status as it is now."""
        entry.card = {
            **entry.card,
            "actions": [proposal_view(self.assistant.store.get(action["proposal_id"])) for action in entry.card["actions"]],
        }
        visible = {
            **entry.card,
            "actions": [
                {**action, "status": "postponed"} if action["proposal_id"] in self._postponed and action["status"] == "pending" else action
                for action in entry.card["actions"]
            ],
        }
        try:
            await self.api.edit_message(entry.chat_id, entry.message_id, card_text(visible, entry.number), card_keyboard(visible))
        except TelegramError as exc:  # "message is not modified" is fine
            log.info("card not redrawn: %s", exc)


def _actor(update: dict[str, Any]) -> str:
    person = update.get("from") or {}
    name = " ".join(part for part in (person.get("first_name"), person.get("last_name")) if part) or "Пользователь"
    username = person.get("username")
    return f"{name} (Telegram @{username})" if username else f"{name} (Telegram)"
