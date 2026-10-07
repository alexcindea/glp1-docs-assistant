"""Every error reaches the client in the same ErrorResponse shape."""

from collections.abc import Iterator

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

UNREACHABLE_URL = "postgresql+asyncpg://app:app@127.0.0.1:1/app"


@pytest.fixture
def app() -> FastAPI:
    app = create_app(Settings(database_url=UNREACHABLE_URL))

    async def boom() -> None:
        raise RuntimeError("internal detail that must not leak")

    async def double(n: int) -> int:
        return n * 2

    app.add_api_route("/boom", boom)
    app.add_api_route("/double", double)
    return app


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app, raise_server_exceptions=False) as client:
        yield client


def test_unhandled_error_hides_internals(client: TestClient) -> None:
    response = client.get("/boom")

    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"
    assert "internal detail" not in response.text


def test_unknown_path_uses_the_error_shape(client: TestClient) -> None:
    response = client.get("/no-such-page")

    assert response.status_code == 404
    assert response.json() == {"code": "not_found", "message": "Not Found", "details": []}


def test_invalid_input_names_the_bad_field(client: TestClient) -> None:
    response = client.get("/double", params={"n": "not-a-number"})

    assert response.status_code == 422
    body = response.json()
    assert body["code"] == "invalid_request"
    assert any(detail.startswith("query.n:") for detail in body["details"])


def test_openapi_documents_the_error_shape(app: FastAPI) -> None:
    schema = app.openapi()

    assert "ErrorResponse" in schema["components"]["schemas"]
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    double_responses = schema["paths"]["/double"]["get"]["responses"]
    assert double_responses["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/ErrorResponse"
    }
