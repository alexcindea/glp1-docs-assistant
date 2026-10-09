"""POST /search: ranked chunks with their provenance, through the real app and database."""

import uuid
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.factory import create_app
from tests.factories import FIXTURE_PDF
from tests.test_documents_api import _run_worker_once, _upload


@pytest.fixture
def client(migrated_database_url: str) -> Iterator[TestClient]:
    settings = Settings(database_url=migrated_database_url)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        yield client


def _ready_document(client: TestClient, database_url: str) -> str:
    document_id: str = _upload(client, FIXTURE_PDF.read_bytes())["id"]
    for _ in range(10):  # other tests may have queued jobs too; the worker takes any
        if client.get(f"/documents/{document_id}").json()["status"] == "ready":
            return document_id
        _run_worker_once(database_url)
    raise AssertionError("the document never became ready")


def test_search_returns_the_passage_with_its_page_and_box(
    client: TestClient, migrated_database_url: str
) -> None:
    document_id = _ready_document(client, migrated_database_url)

    response = client.post(
        "/search", json={"query": "how many developed antibodies", "document_ids": [document_id]}
    )

    assert response.status_code == 200
    body = response.json()
    top = body["hits"][0]
    assert top["document_id"] == document_id
    assert "antibodies" in top["chunk"]["text"]
    assert top["ranks"] == {"vector": 1, "bm25": 1}
    (span,) = top["chunk"]["spans"]
    assert span["page"] == 2
    assert 0 <= span["x0"] < span["x1"] <= 1
    assert all(hit["document_id"] == document_id for hit in body["hits"])


def test_searching_an_unknown_document_says_which(client: TestClient) -> None:
    unknown = str(uuid.uuid4())

    response = client.post("/search", json={"query": "dose", "document_ids": [unknown]})

    assert response.status_code == 404
    assert response.json()["code"] == "unknown_documents"
    assert unknown in response.json()["message"]


@pytest.mark.parametrize(
    "body",
    [{"query": "   "}, {"query": "dose", "limit": 0}, {"query": "dose", "document_ids": []}],
)
def test_invalid_searches_are_rejected(client: TestClient, body: dict[str, object]) -> None:
    response = client.post("/search", json=body)

    assert response.status_code == 422
    assert response.json()["code"] == "invalid_request"
