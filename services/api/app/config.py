"""Settings, read from environment variables (and a .env file in development)."""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


def _find_env_file() -> Path | None:
    """The nearest .env above this file: the repo root in development.

    Containers usually have none, which is fine: settings then come from
    environment variables only.
    """
    for folder in Path(__file__).resolve().parents:
        candidate = folder / ".env"
        if candidate.is_file():
            return candidate
    return None


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=_find_env_file(), extra="ignore")

    database_url: str = "postgresql+asyncpg://app:app@127.0.0.1:5432/app"
    db_connect_timeout_seconds: float = 5.0

    # Model backends. "fake" needs no key and no download; real ones arrive with
    # retrieval (embedder) and grounded answers (LLM).
    llm_provider: Literal["fake"] = "fake"
    embedder: Literal["fake"] = "fake"
    embedding_dimensions: int = 384

    worker_poll_interval_seconds: float = 2.0

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["console", "json"] = "console"

    # Unset: no tracing. Phoenix: http://127.0.0.1:6006/v1/traces
    otel_traces_endpoint: str | None = None
    otel_service_name: str = "api"
    otel_project_name: str = "docs-assistant"


@lru_cache
def get_settings() -> Settings:
    return Settings()
