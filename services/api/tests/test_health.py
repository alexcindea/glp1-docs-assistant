from fastapi.testclient import TestClient

from app.config import Settings
from app.db import migration_head
from app.factory import create_app

# Nothing listens on port 1, so connecting fails immediately.
UNREACHABLE_URL = "postgresql+asyncpg://app:s3cret-password@127.0.0.1:1/app"


def make_client(database_url: str) -> TestClient:
    settings = Settings(database_url=database_url, db_connect_timeout_seconds=2)
    return TestClient(create_app(settings), raise_server_exceptions=False)


def test_liveness_does_not_need_the_database() -> None:
    with make_client(UNREACHABLE_URL) as client:
        response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_ready_when_database_is_migrated(migrated_database_url: str) -> None:
    with make_client(migrated_database_url) as client:
        response = client.get("/health/ready")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ready"
    database = body["database"]
    assert database["problems"] == []
    assert database["migration_revision"] == migration_head()
    assert {e["name"]: bool(e["installed_version"]) for e in database["extensions"]} == {
        "vector": True,
        "pg_textsearch": True,
    }
    assert body["models"] == {
        "llm_provider": "fake",
        "embedder": "fake",
        "embedding_dimensions": 384,
    }


def test_not_ready_before_migrations(database_url: str) -> None:
    with make_client(database_url) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    database = response.json()["database"]
    assert database["reachable"] is True
    assert database["migration_revision"] is None
    assert all(e["installed_version"] is None for e in database["extensions"])
    assert database["problems"]
    assert all("alembic upgrade head" in p for p in database["problems"])


def test_not_ready_when_database_is_unreachable() -> None:
    with make_client(UNREACHABLE_URL) as client:
        response = client.get("/health/ready")

    assert response.status_code == 503
    database = response.json()["database"]
    assert database["reachable"] is False
    assert "docker compose up -d db" in database["problems"][0]
    assert "s3cret-password" not in response.text
