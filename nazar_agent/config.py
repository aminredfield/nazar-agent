"""Settings from the environment (or a local .env file)."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from nazar_agent.llm import ChatCompletionsClient, LLMClient, MetaResponsesClient


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    model_api_key: SecretStr
    meta_base_url: str = "https://api.meta.ai/v1"
    analyst_model: str = "muse-spark-1.3"
    llm_timeout_seconds: float = 90.0

    # The skeptic runs on a different model than the analysts. Target: Muse
    # Spark Open on Baseten (chat_completions API, its own key). Until that is
    # available it runs on another Muse Spark version through Meta Model API.
    skeptic_model: str = "muse-spark-1.2"
    skeptic_api: Literal["responses", "chat_completions"] = "responses"
    skeptic_base_url: str | None = None  # defaults to meta_base_url
    skeptic_api_key: SecretStr | None = None  # defaults to model_api_key

    nazar_base_url: str
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

    @property
    def allowed_chat_ids(self) -> frozenset[int]:
        return frozenset(int(part) for part in self.telegram_allowed_chats.split(",") if part.strip())

    @property
    def has_service_account(self) -> bool:
        return bool(self.nazar_email and self.nazar_password.get_secret_value())

    def analyst_llm(self) -> MetaResponsesClient:
        return MetaResponsesClient(
            api_key=self.model_api_key.get_secret_value(),
            base_url=self.meta_base_url,
            timeout_seconds=self.llm_timeout_seconds,
        )

    def skeptic_llm(self) -> LLMClient:
        key = (self.skeptic_api_key or self.model_api_key).get_secret_value()
        base_url = self.skeptic_base_url or self.meta_base_url
        client_class = ChatCompletionsClient if self.skeptic_api == "chat_completions" else MetaResponsesClient
        return client_class(api_key=key, base_url=base_url, timeout_seconds=self.llm_timeout_seconds)


@lru_cache
def get_settings() -> Settings:
    return Settings()
