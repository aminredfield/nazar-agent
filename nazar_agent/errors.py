"""What the owner reads when a question fails: what happened and what to do, not a class name.

The window, the bot and the live feed all say the same thing, in the
language of the window (the bot speaks Russian); the details go to the log.
"""

from __future__ import annotations

from typing import Literal

import httpx
import openai

from nazar_agent.engine import ToolExecutionError
from nazar_agent.nazar import NazarApiError
from nazar_agent.nazar.client import SESSION_EXPIRED

WindowLang = Literal["ru", "uz", "en"]
MESSAGES: dict[str, dict[WindowLang, str]] = {
    "busy": {
        "ru": "Я ещё отвечаю на предыдущий вопрос. Дождитесь ответа и спросите снова.",
        "uz": "Men hali oldingi savolga javob beryapman. Javobni kuting va qaytadan so'rang.",
        "en": "I am still answering the previous question. Wait for the answer and ask again.",
    },
    "model_slow": {
        "ru": "Модель ИИ долго не отвечает — так бывает при нагрузке. Спросите ещё раз через минуту.",
        "uz": "SI modeli uzoq javob bermayapti — yuklama paytida shunday bo'ladi. Bir daqiqadan keyin qaytadan so'rang.",
        "en": "The AI model is slow to answer — it happens under load. Ask again in a minute.",
    },
    "model_limit": {
        "ru": "К модели ИИ сейчас слишком много запросов. Подождите минуту и спросите снова.",
        "uz": "Hozir SI modeliga juda ko'p so'rov bor. Bir daqiqa kuting va qaytadan so'rang.",
        "en": "The AI model has too many requests right now. Wait a minute and ask again.",
    },
    "model_down": {
        "ru": "Модель ИИ сейчас недоступна. Спросите ещё раз через несколько минут.",
        "uz": "SI modeli hozir ishlamayapti. Bir necha daqiqadan keyin qaytadan so'rang.",
        "en": "The AI model is unavailable right now. Ask again in a few minutes.",
    },
    "model_key": {
        "ru": "Модель ИИ не приняла ключ доступа. Сообщите администратору NAZAR.",
        "uz": "SI modeli kirish kalitini qabul qilmadi. NAZAR administratoriga xabar bering.",
        "en": "The AI model did not accept the access key. Tell the NAZAR administrator.",
    },
    "nazar_down": {
        "ru": "NAZAR сейчас не отвечает, поэтому данные не прочитать. Спросите ещё раз через несколько минут.",
        "uz": "NAZAR hozir javob bermayapti, shuning uchun ma'lumotlarni o'qib bo'lmaydi. Bir necha daqiqadan keyin qaytadan so'rang.",
        "en": "NAZAR is not answering right now, so the data cannot be read. Ask again in a few minutes.",
    },
    "nazar_forbidden": {
        "ru": "В NAZAR у вас нет прав на эти данные. Нужны права владельца или администратора компании.",
        "uz": "NAZAR'da sizda bu ma'lumotlarga huquq yo'q. Kompaniya egasi yoki administratori huquqlari kerak.",
        "en": "In NAZAR you have no rights to this data. It needs the company owner's or an administrator's rights.",
    },
    "nazar_refused": {
        "ru": "NAZAR отказал в запросе: {message}",
        "uz": "NAZAR so'rovni rad etdi: {message}",
        "en": "NAZAR refused the request: {message}",
    },
    "session_expired": {
        "ru": SESSION_EXPIRED,
        "uz": "NAZAR sessiyasi tugadi — yordamchini kabinetdan qaytadan oching.",
        "en": "The NAZAR session has expired — open the assistant from the cabinet again.",
    },
    "no_report": {
        "ru": "Команда не смогла собрать ответ. Попробуйте спросить по-другому или короче.",
        "uz": "Jamoa javobni yig'a olmadi. Boshqacha yoki qisqaroq so'rab ko'ring.",
        "en": "The team could not put an answer together. Try asking differently or shorter.",
    },
    "unknown": {
        "ru": "Не получилось ответить из-за внутренней ошибки — она записана в журнал. Попробуйте ещё раз.",
        "uz": "Ichki xato sababli javob berib bo'lmadi — u jurnalga yozildi. Qaytadan urinib ko'ring.",
        "en": "An internal error kept me from answering — it is in the log. Try again.",
    },
}
BUSY = MESSAGES["busy"]["ru"]
MODEL_SLOW = MESSAGES["model_slow"]["ru"]
MODEL_LIMIT = MESSAGES["model_limit"]["ru"]
MODEL_DOWN = MESSAGES["model_down"]["ru"]
MODEL_KEY = MESSAGES["model_key"]["ru"]
NAZAR_DOWN = MESSAGES["nazar_down"]["ru"]
NAZAR_FORBIDDEN = MESSAGES["nazar_forbidden"]["ru"]
NAZAR_REFUSED = MESSAGES["nazar_refused"]["ru"]
NO_REPORT = MESSAGES["no_report"]["ru"]
UNKNOWN = MESSAGES["unknown"]["ru"]


def owner_message(exc: BaseException, lang: WindowLang = "ru") -> str:
    """A failed question, in words for the owner."""
    return MESSAGES[_kind(exc)][lang].format(message=_refusal(exc))


def _kind(exc: BaseException) -> str:
    from nazar_agent.assistant import (
        AssistantBusy,  # the assistant uses this module too
    )
    from nazar_agent.findings import ReportError

    if isinstance(exc, AssistantBusy):
        return "busy"
    if isinstance(exc, openai.APITimeoutError):
        return "model_slow"
    if isinstance(exc, openai.RateLimitError):
        return "model_limit"
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return "model_key"
    if isinstance(exc, (openai.APIConnectionError, openai.InternalServerError)):
        return "model_down"
    if isinstance(exc, ReportError):
        return "no_report"
    nazar = _nazar_error(exc)
    if nazar is not None:
        return _nazar(nazar.status_code)
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)) or (
        isinstance(exc, ToolExecutionError) and isinstance(exc.__cause__, httpx.HTTPError)
    ):
        return "nazar_down"
    return "unknown"


def _nazar_error(exc: BaseException) -> NazarApiError | None:
    if isinstance(exc, NazarApiError):
        return exc
    if isinstance(exc, ToolExecutionError) and isinstance(exc.__cause__, NazarApiError):
        return exc.__cause__
    return None


def _refusal(exc: BaseException) -> str:
    nazar = _nazar_error(exc)
    return nazar.message if nazar is not None else ""


def _nazar(status: int) -> str:
    if status == 401:
        return "session_expired"
    if status == 403:
        return "nazar_forbidden"
    if status >= 500:
        return "nazar_down"
    return "nazar_refused"
