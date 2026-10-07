"""Shared fixtures.

Database tests run against a real Postgres built from services/db (the same image
docker compose uses), started once per session. Each test gets its own fresh
database inside that server, so tests never see each other's data.
"""

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from testcontainers.core.container import DockerContainer
from testcontainers.core.image import DockerImage
from testcontainers.core.wait_strategies import ExecWaitStrategy

from app.config import Settings

API_DIR = Path(__file__).resolve().parent.parent
DB_IMAGE_DIR = API_DIR.parent / "db"
DB_USER = "test"
DB_PASSWORD = "test"  # noqa: S105 - throwaway container


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _ignore_dotenv(monkeypatch: pytest.MonkeyPatch) -> None:
    """Tests see code defaults plus what they set, never a developer's .env."""
    monkeypatch.setitem(Settings.model_config, "env_file", None)


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Tag every test that needs Docker, so `pytest -m "not db"` runs without it."""
    for item in items:
        if "pg_container" in getattr(item, "fixturenames", ()):
            item.add_marker(pytest.mark.db)


@pytest.fixture(scope="session")
def pg_container() -> Iterator[DockerContainer]:
    with DockerImage(path=DB_IMAGE_DIR, tag="docs-assistant-db:test", clean_up=False) as image:
        container = (
            DockerContainer(str(image))
            .with_envs(POSTGRES_USER=DB_USER, POSTGRES_PASSWORD=DB_PASSWORD, POSTGRES_DB=DB_USER)
            .with_exposed_ports(5432)
            # During first-run init Postgres listens on a local socket only, so a TCP
            # check succeeds only once the real server is up.
            .waiting_for(ExecWaitStrategy(["pg_isready", "-h", "127.0.0.1", "-U", DB_USER]))
        )
        with container:
            yield container


@pytest.fixture
def database_url(pg_container: DockerContainer) -> str:
    """A new, empty database (no migrations applied)."""
    name = f"t_{uuid.uuid4().hex[:12]}"
    result = pg_container.exec(["psql", "-U", DB_USER, "-c", f'CREATE DATABASE "{name}"'])
    assert result.exit_code == 0, result.output
    host = pg_container.get_container_host_ip()
    port = pg_container.get_exposed_port(5432)
    return f"postgresql+asyncpg://{DB_USER}:{DB_PASSWORD}@{host}:{port}/{name}"


def alembic_config(url: str) -> Config:
    config = Config(str(API_DIR / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", url)
    return config


@pytest.fixture
def migrated_database_url(database_url: str) -> str:
    command.upgrade(alembic_config(database_url), "head")
    return database_url
