"""Settings, read from environment variables (and a .env file in development)."""

from functools import lru_cache
from pathlib import Path

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

    database_url: str = "postgresql+asyncpg://app:app@localhost:5432/app"
    db_connect_timeout_seconds: float = 5.0


@lru_cache
def get_settings() -> Settings:
    return Settings()
