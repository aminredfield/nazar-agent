"""Settings from the environment (or a local .env file)."""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta
from functools import lru_cache
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from nazar_agent.llm import ChatCompletionsClient, LLMClient, MetaResponsesClient

log = logging.getLogger(__name__)
COMPANY_TZ = ZoneInfo("Asia/Tashkent")


WORKING_MORNING = time(11, 30)


def working_morning(real: datetime) -> datetime:
    """11:30 of the latest weekday (Monday to Friday) on or before this local day."""
    day = real.astimezone(COMPANY_TZ).date()
    while day.isoweekday() > 5:
        day -= timedelta(days=1)
    return datetime.combine(day, WORKING_MORNING, tzinfo=COMPANY_TZ)


Effort = Literal["minimal", "low", "medium", "high", "xhigh"]
# Measured on the question bank (python -m nazar_agent.evals.bank): the fastest
# setting per role that keeps its answers right.
REASONING_EFFORT: dict[str, Effort] = {"dispatcher": "minimal", "setup": "low"}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    model_api_key: SecretStr
    meta_base_url: str = "https://api.meta.ai/v1"
    analyst_model: str = "muse-spark-1.3"
    # The skeptic on open weights reasons before it answers; 90 s timed out in evals.
    llm_timeout_seconds: float = 150.0
    # How long each role thinks before it answers (the model's reasoning effort:
    # minimal, low, medium, high, xhigh). Thinking is most of an answer's time; a
    # role not listed lets the model choose. In the environment as JSON:
    # REASONING_EFFORT='{"writer": "low"}' replaces the whole map.
    reasoning_effort: dict[str, Effort] = Field(default_factory=lambda: dict(REASONING_EFFORT))

    # The skeptic runs on a different model than the analysts. Target: Muse
    # Spark Open on Baseten (chat_completions API, its own key). Until that is
    # available it runs on another Muse Spark version through Meta Model API.
    skeptic_model: str = "muse-spark-1.2"
    skeptic_api: Literal["responses", "chat_completions"] = "responses"
    skeptic_base_url: str | None = None  # defaults to meta_base_url
    skeptic_api_key: SecretStr | None = None  # defaults to model_api_key
    # Without a key of its own the skeptic (and the photo inspector) runs on
    # this Meta model through the Meta key, whatever else SKEPTIC_* says.
    skeptic_fallback_model: str = "muse-spark-1.2"

    # The demo construction company, built in this process (nazar_agent.demo):
    # no NAZAR, no account — only the model key is needed. Off: the real NAZAR.
    demo_mode: bool = False
    # The demo standing still at this moment, for the agent too: evals that
    # give the same answers every run, a video recorded at 11:30 on a weekday.
    demo_now: datetime | None = None
    # "workday": the demo always shows the latest weekday at 11:30 Tashkent time,
    # today's date or the Friday before — a judge in another time zone never
    # finds a company asleep. "real": Tashkent's own clock.
    demo_clock: Literal["real", "workday"] = "real"
    nazar_base_url: str = ""
    # The assistant key NAZAR shares with this service (AI_AGENT_KEY there):
    # only with it is the cabinet's pass exchanged for the person's session.
    nazar_agent_key: SecretStr = SecretStr("")
    # A service account, for a local setup and the Telegram bot. The window
    # opened from the cabinet never uses it.
    nazar_email: str = ""
    nazar_password: SecretStr = SecretStr("")
    nazar_timeout_seconds: float = 20.0
    # Production: the window works only for people who opened it from NAZAR.
    # Off for a local setup, where the window falls back to the service account.
    require_sign_in: bool = False
    # The window's session cookie; must be true behind HTTPS.
    cookie_secure: bool = False
    # Where the window listens. Locally only this machine; in a container 0.0.0.0
    # behind the reverse proxy.
    service_host: str = "127.0.0.1"
    service_port: int = 8100

    # Proposals and the audit trail (SQLite).
    agent_db_path: str = "nazar_agent.sqlite3"
    # A public demo: questions per window session and dollars per day for
    # everyone together (nazar_agent.limits). 0 = no limit.
    demo_questions_per_session: int = 0
    demo_budget_usd_per_day: float = 0.0

    # Voice messages: Muse Spark 1.2 takes audio (Muse Voice Transcribe has no
    # Russian or Uzbek).
    voice_model: str = "muse-spark-1.2"

    # Telegram: the agent's own bot. Only these chat ids may use it.
    telegram_bot_token: SecretStr | None = None
    telegram_allowed_chats: str = ""  # comma-separated chat ids
    # Only where api.telegram.org is blocked: http://host:port or socks5://host:port
    telegram_proxy: str = ""
    # Morning brief to every allowed chat at this local time, e.g. "08:30"; empty = off.
    brief_time: str = ""

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if self.demo_now is not None and not self.demo_mode:
            raise ValueError("DEMO_NOW only stops the demo's clock; set DEMO_MODE=true too")
        if self.demo_now is not None and self.demo_now.tzinfo is None:
            self.demo_now = self.demo_now.replace(tzinfo=COMPANY_TZ)
        if not self.demo_mode and not self.nazar_base_url:
            raise ValueError("NAZAR_BASE_URL is required; or set DEMO_MODE=true to run on the demo company")
        own_key = self.skeptic_api_key is not None and bool(self.skeptic_api_key.get_secret_value())
        elsewhere = self.skeptic_api != "responses" or self.skeptic_base_url not in (None, "", self.meta_base_url)
        if elsewhere and not own_key:
            # A Baseten model with the Meta key would only fail: use Meta's own model instead.
            log.warning("SKEPTIC_API_KEY is empty: the skeptic runs on %s through the Meta API", self.skeptic_fallback_model)
            self.skeptic_model, self.skeptic_api, self.skeptic_base_url = self.skeptic_fallback_model, "responses", None
        return self

    def now(self) -> datetime:
        """The company's present moment: real time, the demo's stopped clock, or its working morning."""
        if self.demo_now is not None:
            return self.demo_now.astimezone(COMPANY_TZ)
        real = datetime.now(COMPANY_TZ)
        if self.demo_mode and self.demo_clock == "workday":
            return working_morning(real)
        return real

    @property
    def demo_clock_runs(self) -> bool:
        """Whether the demo NAZAR follows this settings' clock rather than the real one."""
        return self.demo_now is not None or (self.demo_mode and self.demo_clock == "workday")

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        return frozenset(int(part) for part in self.telegram_allowed_chats.split(",") if part.strip())

    @property
    def has_service_account(self) -> bool:
        return self.demo_mode or bool(self.nazar_email and self.nazar_password.get_secret_value())

    @property
    def window_needs_sign_in(self) -> bool:
        """The demo window opens at once as the demo company's owner; a real one may need NAZAR."""
        return self.require_sign_in and not self.demo_mode

    def analyst_llm(self) -> MetaResponsesClient:
        return MetaResponsesClient(
            api_key=self.model_api_key.get_secret_value(),
            base_url=self.meta_base_url,
            timeout_seconds=self.llm_timeout_seconds,
            efforts=self.reasoning_effort,
        )

    def skeptic_llm(self) -> LLMClient:
        key = (self.skeptic_api_key or self.model_api_key).get_secret_value()
        base_url = self.skeptic_base_url or self.meta_base_url
        client_class = ChatCompletionsClient if self.skeptic_api == "chat_completions" else MetaResponsesClient
        return client_class(
            api_key=key, base_url=base_url, timeout_seconds=self.llm_timeout_seconds, efforts=self.reasoning_effort
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
