"""Uploading documents: checks, duplicates, status, provenance, and the typed errors."""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import update

from app.config import Settings
from app.db import create_engine, create_sessionmaker
from app.factory import create_app
from app.ingest.chunking import chunk_document
from app.models import Document, Job
from app.providers import build_embedder
from app.worker import IngestWorker
from tests.factories import FIXTURE_PDF, FakeParser, fake_parsed


@pytest.fixture
def client(migrated_database_url: str) -> Iterator[TestClient]:
    settings = Settings(database_url=migrated_database_url, max_upload_mb=1, max_pages=50)
    with TestClient(create_app(settings), raise_server_exceptions=False) as client:
        yield client


def _upload(client: TestClient, data: bytes, name: str = "label.pdf") -> dict[str, Any]:
    response = client.post("/documents", files={"file": (name, data, "application/pdf")})
    return {"http": response.status_code, **response.json()}


def _run_worker_once(database_url: str) -> None:
    """What the separate worker process would do: take one job and finish it."""

    async def run() -> None:
        engine = create_engine(Settings(database_url=database_url))
        settings = Settings()
        worker = IngestWorker(
            settings, create_sessionmaker(engine), FakeParser, build_embedder(settings)
        )
        await worker.poll()
        await engine.dispose()

    asyncio.run(run())


def _sql(database_url: str, statement: Any) -> None:
    async def run() -> None:
        engine = create_engine(Settings(database_url=database_url))
        async with create_sessionmaker(engine).begin() as session:
            await session.execute(statement)
        await engine.dispose()

    asyncio.run(run())


def test_upload_queues_the_document(client: TestClient) -> None:
    body = _upload(client, FIXTURE_PDF.read_bytes())

    assert (body["http"], body["status"]) == (202, "queued")
    assert body["filename"] == "label.pdf"
    assert body["page_count"] == 2
    assert body["active_job"] == {
        "kind": "ingest",
        "status": "queued",
        "pages_done": 0,
        "pages_total": 2,
    }
    assert client.get(f"/documents/{body['id']}").json()["status"] == "queued"


def test_the_same_file_twice_returns_the_same_document(client: TestClient) -> None:
    first = _upload(client, FIXTURE_PDF.read_bytes(), "a.pdf")
    second = _upload(client, FIXTURE_PDF.read_bytes(), "b.pdf")

    assert (first["http"], second["http"]) == (202, 200)
    assert second["id"] == first["id"]
    assert second["filename"] == "a.pdf"
    assert len(client.get("/documents").json()) == 1


def test_a_failed_document_is_queued_again_when_uploaded_again(
    client: TestClient, migrated_database_url: str
) -> None:
    first = _upload(client, FIXTURE_PDF.read_bytes())
    _sql(
        migrated_database_url,
        update(Job).where(Job.document_id == first["id"]).values(status="failed"),
    )
    _sql(
        migrated_database_url,
        update(Document)
        .where(Document.id == first["id"])
        .values(status="failed", error_code="parse_failed", error="Unreadable."),
    )

    again = _upload(client, FIXTURE_PDF.read_bytes())

    assert (again["http"], again["id"], again["status"]) == (202, first["id"], "queued")
    assert (again["error_code"], again["error"]) == (None, None)
    assert again["active_job"]["kind"] == "ingest"


def test_chunks_come_back_with_their_provenance(
    client: TestClient, migrated_database_url: str
) -> None:
    document_id = _upload(client, FIXTURE_PDF.read_bytes())["id"]
    _run_worker_once(migrated_database_url)

    document = client.get(f"/documents/{document_id}").json()
    chunks = client.get(f"/documents/{document_id}/chunks").json()

    assert (document["status"], document["active_job"]) == ("ready", None)
    expected = chunk_document(fake_parsed())
    assert [c["text"] for c in chunks] == [c.text for c in expected]
    assert [c["seq"] for c in chunks] == list(range(len(expected)))
    for chunk, draft in zip(chunks, expected, strict=True):
        assert (chunk["kind"], chunk["section_path"]) == (draft.kind, draft.section_path)
        assert [
            (s["page"], s["x0"], s["y0"], s["x1"], s["y1"], s["char_start"], s["char_end"])
            for s in chunk["spans"]
        ] == [
            (r.page, r.bbox.x0, r.bbox.y0, r.bbox.x1, r.bbox.y1, r.char_start, r.char_end)
            for r in draft.spans
        ]


def test_rechunk_is_accepted_once_at_a_time(client: TestClient, migrated_database_url: str) -> None:
    document_id = _upload(client, FIXTURE_PDF.read_bytes())["id"]
    _run_worker_once(migrated_database_url)

    first = client.post(f"/documents/{document_id}/rechunk")
    second = client.post(f"/documents/{document_id}/rechunk")

    assert first.status_code == 202
    assert first.json()["status"] == "ready"  # still searchable meanwhile
    assert first.json()["active_job"]["kind"] == "rechunk"
    assert (second.status_code, second.json()["code"]) == (409, "already_processing")


@pytest.mark.parametrize(
    ("data", "status", "code"),
    [
        (b"hello, not a pdf", 415, "not_a_pdf"),
        (b"%PDF-1.7\n this is not really a pdf", 400, "unreadable_pdf"),
        (b"%PDF-1.7\n" + b"0" * (1024 * 1024 + 1), 413, "too_large"),  # caught by the route
        (b"%PDF-1.7\n" + b"0" * (2 * 1024 * 1024), 413, "too_large"),  # caught before reading
    ],
    ids=["not-a-pdf", "damaged", "too-large", "too-large-declared"],
)
def test_bad_files_are_rejected_with_a_typed_error(
    client: TestClient, data: bytes, status: int, code: str
) -> None:
    body = _upload(client, data)

    assert body["http"] == status
    assert body["code"] == code
    assert body["message"]
    assert client.get("/documents").json() == []


def test_too_many_pages_is_rejected(migrated_database_url: str) -> None:
    settings = Settings(database_url=migrated_database_url, max_pages=1)
    with TestClient(create_app(settings)) as client:
        body = _upload(client, FIXTURE_PDF.read_bytes())

    assert (body["http"], body["code"]) == (400, "too_many_pages")


def test_unknown_document_is_a_typed_404(client: TestClient) -> None:
    response = client.get("/documents/00000000-0000-0000-0000-000000000000")

    assert response.status_code == 404
    assert response.json()["code"] == "not_found"


def test_a_document_not_yet_ingested_has_no_chunks_and_cannot_be_rechunked(
    client: TestClient,
) -> None:
    document_id = _upload(client, FIXTURE_PDF.read_bytes())["id"]

    assert client.get(f"/documents/{document_id}/chunks").json() == []
    response = client.post(f"/documents/{document_id}/rechunk")
    assert (response.status_code, response.json()["code"]) == (409, "not_ready")
