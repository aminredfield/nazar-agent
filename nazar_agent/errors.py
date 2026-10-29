"""What the owner reads when a question fails: what happened and what to do, not a class name.

The window, the bot and the live feed all say the same thing; the details go
to the log.
"""

from __future__ import annotations

import httpx
import openai

from nazar_agent.engine import ToolExecutionError
from nazar_agent.nazar import NazarApiError
from nazar_agent.nazar.client import SESSION_EXPIRED

BUSY = "Я ещё отвечаю на предыдущий вопрос. Дождитесь ответа и спросите снова."
MODEL_SLOW = "Модель ИИ долго не отвечает — так бывает при нагрузке. Спросите ещё раз через минуту."
MODEL_LIMIT = "К модели ИИ сейчас слишком много запросов. Подождите минуту и спросите снова."
MODEL_DOWN = "Модель ИИ сейчас недоступна. Спросите ещё раз через несколько минут."
MODEL_KEY = "Модель ИИ не приняла ключ доступа. Сообщите администратору NAZAR."
NAZAR_DOWN = "NAZAR сейчас не отвечает, поэтому данные не прочитать. Спросите ещё раз через несколько минут."
NAZAR_FORBIDDEN = "В NAZAR у вас нет прав на эти данные. Нужны права владельца или администратора компании."
NAZAR_REFUSED = "NAZAR отказал в запросе: {message}"
NO_REPORT = "Команда не смогла собрать ответ. Попробуйте спросить по-другому или короче."
UNKNOWN = "Не получилось ответить из-за внутренней ошибки — она записана в журнал. Попробуйте ещё раз."


def owner_message(exc: BaseException) -> str:
    """A failed question, in words for the owner."""
    from nazar_agent.assistant import AssistantBusy  # the assistant uses this module too
    from nazar_agent.findings import ReportError

    if isinstance(exc, AssistantBusy):
        return BUSY
    if isinstance(exc, openai.APITimeoutError):
        return MODEL_SLOW
    if isinstance(exc, openai.RateLimitError):
        return MODEL_LIMIT
    if isinstance(exc, (openai.AuthenticationError, openai.PermissionDeniedError)):
        return MODEL_KEY
    if isinstance(exc, (openai.APIConnectionError, openai.InternalServerError)):
        return MODEL_DOWN
    if isinstance(exc, ReportError):
        return NO_REPORT
    if isinstance(exc, NazarApiError):
        return _nazar(exc.status_code, exc.message)
    if isinstance(exc, ToolExecutionError) and isinstance(exc.__cause__, NazarApiError):
        return _nazar(exc.__cause__.status_code, exc.__cause__.message)
    if isinstance(exc, (httpx.TimeoutException, httpx.TransportError)) or (
        isinstance(exc, ToolExecutionError) and isinstance(exc.__cause__, httpx.HTTPError)
    ):
        return NAZAR_DOWN
    return UNKNOWN


def _nazar(status: int, message: str) -> str:
    if status == 401:
        return SESSION_EXPIRED
    if status == 403:
        return NAZAR_FORBIDDEN
    if status >= 500:
        return NAZAR_DOWN
    return NAZAR_REFUSED.format(message=message)
