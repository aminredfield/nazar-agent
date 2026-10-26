"""Settings from the environment (or a local .env file)."""

from __future__ import annotations

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    model_api_key: SecretStr
    meta_base_url: str = "https://api.meta.ai/v1"
    analyst_model: str = "muse-spark-1.3"
    llm_timeout_seconds: float = 90.0

    nazar_base_url: str
    nazar_email: str
    nazar_password: SecretStr
    nazar_timeout_seconds: float = 20.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
