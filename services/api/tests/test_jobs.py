"""The Postgres job queue: claiming, concurrency, reclaiming dead workers, retries."""

import asyncio
from datetime import timedelta

import pytest
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingest.failures import Failure
from app.jobs import claim_next, enqueue, heartbeat, mark_failed_attempt, mark_succeeded
from app.models import Document, Job, JobKind
from tests.factories import insert_document

pytestmark = pytest.mark.anyio

STALE = timedelta(seconds=60)
TRANSIENT = Failure(
    code="ingest_failed", message="Try later.", detail="TimeoutError: x", retry=True
)


async def _job(sessions: async_sessionmaker[AsyncSession], job_id: int) -> Job:
    async with sessions() as session:
        job = await session.get(Job, job_id)
        assert job is not None
        return job


async def _document_status(sessions: async_sessionmaker[AsyncSession], job: Job) -> str:
    async with sessions() as session:
        status = await session.scalar(select(Document.status).where(Document.id == job.document_id))
        assert status is not None
        return status


async def test_claim_takes_the_oldest_job_once(sessions: async_sessionmaker[AsyncSession]) -> None:
    first = await insert_document(sessions)
    second = await insert_document(sessions)

    a = await claim_next(sessions, "worker-a", stale_after=STALE)
    b = await claim_next(sessions, "worker-b", stale_after=STALE)
    c = await claim_next(sessions, "worker-c", stale_after=STALE)

    assert a is not None
    assert b is not None
    assert (a.document_id, b.document_id) == (first, second)
    assert c is None
    job = await _job(sessions, a.id)
    assert (job.status, job.locked_by, job.attempts) == ("running", "worker-a", 1)
    assert await _document_status(sessions, job) == "processing"


async def test_concurrent_workers_never_claim_the_same_job(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await insert_document(sessions)

    claims = await asyncio.gather(
        *(claim_next(sessions, f"worker-{i}", stale_after=STALE) for i in range(8))
    )

    assert sum(claim is not None for claim in claims) == 1


async def test_a_dead_workers_job_is_reclaimed(sessions: async_sessionmaker[AsyncSession]) -> None:
    await insert_document(sessions)
    claimed = await claim_next(sessions, "worker-a", stale_after=STALE)
    assert claimed is not None
    async with sessions.begin() as session:  # worker-a stops heartbeating
        await session.execute(
            update(Job)
            .where(Job.id == claimed.id)
            .values(heartbeat_at=Job.heartbeat_at - STALE * 2)
        )

    assert await heartbeat(sessions, claimed.id, "worker-a", done=1, total=2)  # got halfway
    async with sessions.begin() as session:
        await session.execute(
            update(Job)
            .where(Job.id == claimed.id)
            .values(heartbeat_at=Job.heartbeat_at - STALE * 2)
        )

    reclaimed = await claim_next(sessions, "worker-b", stale_after=STALE)

    assert reclaimed is not None
    assert (reclaimed.id, reclaimed.attempt, reclaimed.pages) == (claimed.id, 2, 2)
    assert (await _job(sessions, claimed.id)).progress_done == 0  # starts over
    # worker-a, if it was only slow, finds out and can't overwrite worker-b's work.
    assert not await heartbeat(sessions, claimed.id, "worker-a", done=1, total=2)
    async with sessions.begin() as session:
        assert not await mark_succeeded(session, claimed.id, "worker-a")
        assert await mark_succeeded(session, claimed.id, "worker-b")


async def test_a_job_that_keeps_killing_workers_is_failed(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await insert_document(sessions, max_attempts=1)
    claimed = await claim_next(sessions, "worker-a", stale_after=STALE)
    assert claimed is not None
    async with sessions.begin() as session:
        await session.execute(
            update(Job)
            .where(Job.id == claimed.id)
            .values(heartbeat_at=Job.heartbeat_at - STALE * 2)
        )

    assert await claim_next(sessions, "worker-b", stale_after=STALE) is None
    job = await _job(sessions, claimed.id)
    assert job.status == "failed"
    assert job.error is not None
    async with sessions() as session:
        document = await session.get(Document, job.document_id)
    assert document is not None
    assert (document.status, document.error_code) == ("failed", "worker_crashed")


async def test_failures_are_retried_then_given_up(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await insert_document(sessions, max_attempts=2)

    for attempt in (1, 2):
        claimed = await claim_next(sessions, "worker-a", stale_after=STALE)
        assert claimed is not None
        assert claimed.attempt == attempt
        await mark_failed_attempt(sessions, claimed.id, "worker-a", TRANSIENT, backoff=timedelta(0))
        job = await _job(sessions, claimed.id)
        if attempt == 1:
            assert (job.status, job.locked_by) == ("queued", None)
            assert await _document_status(sessions, job) == "queued"

    assert (job.status, job.error) == ("failed", "TimeoutError: x")
    assert await _document_status(sessions, job) == "failed"
    assert await claim_next(sessions, "worker-a", stale_after=STALE) is None


async def test_a_retry_waits_for_its_backoff(sessions: async_sessionmaker[AsyncSession]) -> None:
    await insert_document(sessions)
    claimed = await claim_next(sessions, "worker-a", stale_after=STALE)
    assert claimed is not None

    await mark_failed_attempt(
        sessions, claimed.id, "worker-a", TRANSIENT, backoff=timedelta(minutes=5)
    )

    assert await claim_next(sessions, "worker-a", stale_after=STALE) is None


async def test_a_failure_that_would_only_repeat_is_not_retried(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    await insert_document(sessions, max_attempts=3)
    claimed = await claim_next(sessions, "worker-a", stale_after=STALE)
    assert claimed is not None
    permanent = Failure(
        code="parse_failed", message="Unreadable.", detail="ParseError: x", retry=False
    )

    await mark_failed_attempt(sessions, claimed.id, "worker-a", permanent, backoff=timedelta(0))

    job = await _job(sessions, claimed.id)
    assert (job.status, job.attempts) == ("failed", 1)
    async with sessions() as session:
        document = await session.get(Document, job.document_id)
    assert document is not None
    assert (document.status, document.error_code, document.error) == (
        "failed",
        "parse_failed",
        "Unreadable.",
    )


async def test_a_document_can_have_only_one_active_job(
    sessions: async_sessionmaker[AsyncSession],
) -> None:
    document_id = await insert_document(sessions)  # already has a queued ingest job

    with pytest.raises(IntegrityError):
        async with sessions.begin() as session:
            enqueue(session, document_id, JobKind.RECHUNK, max_attempts=3, pages=0)
