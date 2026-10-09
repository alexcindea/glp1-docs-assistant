"""Ingest jobs end to end: claim, parse, chunk, store with provenance, finish or retry."""

import asyncio
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from app.config import Settings
from app.db import create_engine, create_sessionmaker
from app.ingest.failures import ParseError
from app.ingest.models import ParsedDocument
from app.jobs import enqueue
from app.models import Chunk, Document, Job, JobKind, PageRecord, ParsedRecord, TableRecord
from app.worker import IngestWorker, docling_parser_factory
from tests.factories import FakeParser, insert_document

pytestmark = pytest.mark.anyio

Sessions = async_sessionmaker[AsyncSession]


def _worker(sessions: Sessions, parser: FakeParser, **settings: Any) -> IngestWorker:
    config = Settings(job_heartbeat_seconds=0.01, **settings)
    return IngestWorker(config, sessions, lambda: parser)


async def _document(sessions: Sessions, document_id: uuid.UUID) -> Document:
    async with sessions() as session:
        document = await session.get(Document, document_id)
        assert document is not None
        return document


async def _job(sessions: Sessions, document_id: uuid.UUID, kind: JobKind) -> Job:
    async with sessions() as session:
        job = await session.scalar(
            select(Job).where(Job.document_id == document_id, Job.kind == kind.value)
        )
        assert job is not None
        return job


async def _chunks(sessions: Sessions, document_id: uuid.UUID) -> list[Chunk]:
    async with sessions() as session:
        return list(
            await session.scalars(
                select(Chunk)
                .where(Chunk.document_id == document_id)
                .order_by(Chunk.seq)
                .options(selectinload(Chunk.spans))
            )
        )


async def _count(sessions: Sessions, model: Any, document_id: uuid.UUID) -> int:
    async with sessions() as session:
        result = await session.scalar(
            select(func.count()).select_from(model).where(model.document_id == document_id)
        )
        return int(result or 0)


async def test_ingest_stores_pages_tables_and_chunks_with_provenance(sessions: Sessions) -> None:
    document_id = await insert_document(sessions)
    parser = FakeParser()

    result = await _worker(sessions, parser).poll()

    assert result.worked
    assert parser.calls == 1
    document = await _document(sessions, document_id)
    assert (document.status, document.scanned_pages, document.parser) == ("ready", 1, "fake parser")
    assert await _count(sessions, PageRecord, document_id) == 2
    assert await _count(sessions, ParsedRecord, document_id) == 1
    async with sessions() as session:
        table = await session.scalar(
            select(TableRecord).where(TableRecord.document_id == document_id)
        )
    assert table is not None
    assert table.caption == "Table 1: Strengths"
    assert table.cells[0]["text"] == "5 mg"
    job = await _job(sessions, document_id, JobKind.INGEST)
    assert (job.status, job.progress_done, job.progress_total) == ("succeeded", 2, 2)

    chunks = await _chunks(sessions, document_id)
    assert [c.seq for c in chunks] == list(range(len(chunks)))
    assert [c.table_id for c in chunks if c.kind == "table"] == [table.id]
    scanned = next(c for c in chunks if "antibodies" in c.text)
    assert [(s.page, s.char_start, s.char_end) for s in scanned.spans] == [
        (2, 0, len(scanned.text))
    ]


async def test_rechunk_reuses_the_stored_parse_and_keeps_the_document_ready(
    sessions: Sessions,
) -> None:
    document_id = await insert_document(sessions)
    await _worker(sessions, FakeParser()).poll()
    before = [c.id for c in await _chunks(sessions, document_id)]
    async with sessions.begin() as session:
        enqueue(session, document_id, JobKind.RECHUNK, max_attempts=3, pages=0)
    no_parsing = FakeParser(fail_with=AssertionError("re-chunking must not parse"))
    worker = _worker(sessions, no_parsing)

    claimed_status: list[str] = []
    original = worker._rechunk

    async def spying_rechunk(job: Any) -> None:
        claimed_status.append((await _document(sessions, document_id)).status)
        await original(job)

    worker._rechunk = spying_rechunk  # type: ignore[method-assign]
    result = await worker.poll()

    assert result.worked
    assert no_parsing.calls == 0
    assert claimed_status == ["ready"]  # chunks stay searchable while it runs
    after = await _chunks(sessions, document_id)
    assert len(after) == len(before)
    assert {c.id for c in after}.isdisjoint(before)  # replaced, not appended
    assert (await _document(sessions, document_id)).status == "ready"


async def test_a_failed_rechunk_keeps_the_current_chunks(
    sessions: Sessions, monkeypatch: pytest.MonkeyPatch
) -> None:
    document_id = await insert_document(sessions)
    await _worker(sessions, FakeParser()).poll()
    before = [c.id for c in await _chunks(sessions, document_id)]
    async with sessions.begin() as session:
        enqueue(session, document_id, JobKind.RECHUNK, max_attempts=1, pages=0)

    def broken_chunker(parsed: ParsedDocument) -> list[Any]:
        raise RuntimeError("chunker bug")

    monkeypatch.setattr("app.worker.chunk_document", broken_chunker)
    await _worker(sessions, FakeParser()).poll()

    assert (await _job(sessions, document_id, JobKind.RECHUNK)).status == "failed"
    document = await _document(sessions, document_id)
    assert (document.status, document.error) == ("ready", None)
    assert [c.id for c in await _chunks(sessions, document_id)] == before


async def test_a_transient_failure_is_retried_then_reported_plainly(sessions: Sessions) -> None:
    document_id = await insert_document(sessions, max_attempts=2)
    broken = FakeParser(fail_with=RuntimeError("connection reset by C:\\tmp\\secret-path"))
    worker = _worker(sessions, broken, job_retry_backoff_seconds=0)

    await worker.poll()
    assert (await _document(sessions, document_id)).status == "queued"
    await worker.poll()

    document = await _document(sessions, document_id)
    assert (document.status, document.error_code) == ("failed", "ingest_failed")
    assert document.error is not None
    assert "secret-path" not in document.error  # details stay in the job record and logs
    assert "secret-path" in ((await _job(sessions, document_id, JobKind.INGEST)).error or "")
    assert broken.calls == 2
    assert await _count(sessions, Chunk, document_id) == 0


async def test_an_unreadable_pdf_fails_at_once(sessions: Sessions) -> None:
    document_id = await insert_document(sessions, max_attempts=3)
    broken = FakeParser(fail_with=ParseError("pages 1-2: unsupported encoding"))

    await _worker(sessions, broken).poll()

    document = await _document(sessions, document_id)
    assert (document.status, document.error_code) == ("failed", "parse_failed")
    assert broken.calls == 1  # retrying would only repeat the same failure


class _StealingParser(FakeParser):
    """While parsing, another worker takes the job over (this one looked dead)."""

    def __init__(self, steal: Callable[[], None]) -> None:
        super().__init__()
        self.steal = steal

    def parse(self, path: Path, **kwargs: Any) -> ParsedDocument:
        self.steal()
        return super().parse(path, **kwargs)


async def test_a_worker_that_lost_its_job_writes_nothing(
    sessions: Sessions, migrated_database_url: str
) -> None:
    document_id = await insert_document(sessions)

    async def reassign() -> None:  # runs on the parsing thread, so its own engine and loop
        engine = create_engine(Settings(database_url=migrated_database_url))
        async with create_sessionmaker(engine).begin() as session:
            await session.execute(
                update(Job).where(Job.document_id == document_id).values(locked_by="worker-b")
            )
        await engine.dispose()

    parser = _StealingParser(lambda: asyncio.run(reassign()))
    result = await _worker(sessions, parser).poll()

    assert result.worked
    assert parser.calls == 1
    job = await _job(sessions, document_id, JobKind.INGEST)
    assert (job.status, job.locked_by) == ("running", "worker-b")  # worker-b's to finish
    assert await _count(sessions, Chunk, document_id) == 0
    assert await _count(sessions, PageRecord, document_id) == 0


@pytest.mark.ingest
async def test_real_docling_ingest_keeps_provenance(sessions: Sessions) -> None:
    pytest.importorskip("docling")
    document_id = await insert_document(sessions)
    settings = Settings(ingest_batch_pages=1)
    worker = IngestWorker(settings, sessions, docling_parser_factory(settings))

    await worker.poll()

    document = await _document(sessions, document_id)
    assert (document.status, document.page_count, document.scanned_pages) == ("ready", 2, 1)
    chunks = await _chunks(sessions, document_id)
    ocr = next(c for c in chunks if "anti-exenatide antibodies" in " ".join(c.text.split()))
    assert {s.page for s in ocr.spans} == {2}
    for chunk in chunks:
        assert chunk.spans
        for span in chunk.spans:
            assert 0 <= span.x0 <= span.x1 <= 1
            assert 0 <= span.y0 <= span.y1 <= 1
