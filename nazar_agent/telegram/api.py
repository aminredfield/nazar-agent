"""A thin client for the Telegram Bot API (https://core.telegram.org/bots/api).

Long polling, so the bot works without a public URL. The token is part of
every URL; it is never logged.
"""

from __future__ import annotations

from typing import Any

import httpx

POLL_TIMEOUT_SECONDS = 30


class TelegramError(Exception):
    pass


class TelegramApi:
    def __init__(
        self,
        token: str,
        *,
        proxy: str | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._token = token
        # Long polling holds the request open for POLL_TIMEOUT_SECONDS. The
        # proxy is for networks where api.telegram.org is blocked (seen on the
        # development machine in Uzbekistan, while the Telegram app works).
        self._http = httpx.AsyncClient(timeout=POLL_TIMEOUT_SECONDS + 15, proxy=proxy or None, transport=transport)

    async def get_updates(self, offset: int | None) -> list[dict[str, Any]]:
        params: dict[str, Any] = {"timeout": POLL_TIMEOUT_SECONDS, "allowed_updates": '["message","callback_query"]'}
        if offset is not None:
            params["offset"] = offset
        return await self._call("getUpdates", params)

    async def send_message(self, chat_id: int, text: str, keyboard: list[list[dict[str, str]]] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True,
        }
        if keyboard is not None:
            payload["reply_markup"] = {"inline_keyboard": keyboard}
        return await self._call("sendMessage", payload)

    async def edit_message(
        self, chat_id: int, message_id: int, text: str, keyboard: list[list[dict[str, str]]]
    ) -> None:
        await self._call(
            "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
                "reply_markup": {"inline_keyboard": keyboard},
            },
        )

    async def edit_keyboard(self, chat_id: int, message_id: int, keyboard: list[list[dict[str, str]]]) -> None:
        await self._call(
            "editMessageReplyMarkup",
            {"chat_id": chat_id, "message_id": message_id, "reply_markup": {"inline_keyboard": keyboard}},
        )

    async def answer_callback(self, callback_id: str, text: str = "") -> None:
        await self._call("answerCallbackQuery", {"callback_query_id": callback_id, "text": text})

    async def send_chat_action(self, chat_id: int, action: str = "typing") -> None:
        await self._call("sendChatAction", {"chat_id": chat_id, "action": action})

    async def download(self, file_id: str) -> bytes:
        file = await self._call("getFile", {"file_id": file_id})
        response = await self._http.get(f"https://api.telegram.org/file/bot{self._token}/{file['file_path']}")
        if response.is_error:
            raise TelegramError(f"download failed: HTTP {response.status_code}")
        return response.content

    async def aclose(self) -> None:
        await self._http.aclose()

    async def _call(self, method: str, payload: dict[str, Any]) -> Any:
        response = await self._http.post(f"https://api.telegram.org/bot{self._token}/{method}", json=payload)
        body = response.json()
        if not body.get("ok"):
            # The description is Telegram's own text; it never contains the token.
            raise TelegramError(f"{method}: {body.get('description', response.status_code)}")
        return body["result"]
