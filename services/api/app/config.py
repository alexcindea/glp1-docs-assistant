"""Settings, read from environment variables (and a .env file in development)."""

import os
import socket
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.llm import ReasoningEffort


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

    # Model backends. "fake" needs no key and no download (tests, CI). The real
    # embedder runs locally (app.local_embedder); the LLM is OpenAI.
    llm_provider: Literal["fake", "openai"] = "fake"
    embedder: Literal["fake", "qwen3-0.6b"] = "fake"

    # OpenAI: the model and reasoning effort for each role (app.llm.ModelRole).
    openai_api_key: SecretStr | None = None
    openai_timeout_seconds: float = 60.0
    llm_answer_model: str = "gpt-6.1-sol"
    llm_answer_effort: ReasoningEffort = "low"
    llm_rewrite_model: str = "gpt-6-luna"
    llm_rewrite_effort: ReasoningEffort = "none"
    llm_judge_model: str = "gpt-6-astra"
    llm_judge_effort: ReasoningEffort = "medium"

    # Uploads
    max_upload_mb: int = 100
    max_pages: int = 2000

    # Ingest worker and job queue
    worker_poll_interval_seconds: float = 2.0
    worker_id: str = Field(default_factory=lambda: f"{socket.gethostname()}-{os.getpid()}")
    ingest_batch_pages: int = 10
    job_heartbeat_seconds: float = 10.0
    # A running job whose heartbeat is older than this is presumed dead and reclaimed.
    job_stale_after_seconds: float = 120.0
    job_max_attempts: int = 3
    job_retry_backoff_seconds: float = 30.0

    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    log_format: Literal["console", "json"] = "console"

    # Unset: no tracing. Phoenix: http://127.0.0.1:6006/v1/traces
    otel_traces_endpoint: str | None = None
    otel_service_name: str = "api"
    otel_project_name: str = "docs-assistant"


@lru_cache
def get_settings() -> Settings:
    return Settings()
