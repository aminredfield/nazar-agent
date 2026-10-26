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
    nazar_email: str
    nazar_password: SecretStr
    nazar_timeout_seconds: float = 20.0

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
