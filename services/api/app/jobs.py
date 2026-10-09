"""The ingest queue: a Postgres table, claimed with FOR UPDATE SKIP LOCKED.

A worker claims the oldest runnable job and heartbeats while it works. A job is
runnable when it is queued and due, or when it is "running" but its heartbeat
is stale: its worker died, so another one takes over. Retryable failures are
retried with a growing delay; after `max_attempts`, or for a failure that would
only repeat (an unreadable PDF), the job is marked failed. Every update after
the claim checks `locked_by`, so a worker that lost its job (stale, then
reclaimed) can't overwrite the new owner's work.

Only ingest jobs change the document's status. A re-chunk leaves it `ready` with
its current chunks, which are replaced in one transaction when it succeeds and
kept as they are if it fails. A partial unique index allows one queued or
running job per document.
"""

import uuid
from dataclasses import dataclass
from datetime import timedelta

from sqlalchemy import and_, func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.ingest.failures import WORKER_CRASHED, Failure
from app.models import Document, DocumentStatus, Job, JobKind, JobStatus


@dataclass(frozen=True)
class ClaimedJob:
    id: int
    document_id: uuid.UUID
    kind: JobKind
    attempt: int
    max_attempts: int
    pages: int  # progress total known at claim time


def enqueue(
    session: AsyncSession, document_id: uuid.UUID, kind: JobKind, *, max_attempts: int, pages: int
) -> Job:
    """Add a job in the caller's transaction, so a document never exists without one.

    Flushing raises IntegrityError if the document already has an active job.
    """
    job = Job(
        document_id=document_id,
        kind=kind.value,
        max_attempts=max_attempts,
        progress_total=pages,
    )
    session.add(job)
    return job


async def claim_next(
    sessions: async_sessionmaker[AsyncSession], worker_id: str, *, stale_after: timedelta
) -> ClaimedJob | None:
    """Take the oldest runnable job, or None. Safe with any number of concurrent workers."""
    async with sessions.begin() as session:
        while True:
            stale = and_(
                Job.status == JobStatus.RUNNING.value,
                Job.heartbeat_at < func.now() - stale_after,
            )
            due = and_(Job.status == JobStatus.QUEUED.value, Job.run_after <= func.now())
            job = (
                await session.execute(
                    select(Job)
                    .where(or_(due, stale))
                    .order_by(Job.created_at, Job.id)
                    .limit(1)
                    .with_for_update(skip_locked=True)
                )
            ).scalar_one_or_none()
            if job is None:
                return None
            if job.status == JobStatus.RUNNING.value and job.attempts >= job.max_attempts:
                # Its worker died on every attempt (e.g. out of memory): stop retrying.
                await _mark_failed(session, job, WORKER_CRASHED)
                continue
            job.status = JobStatus.RUNNING.value
            job.attempts += 1
            job.locked_by = worker_id
            job.heartbeat_at = func.now()
            job.started_at = func.now()
            job.error = None
            job.progress_done = 0  # a reclaimed job starts over
            if job.kind == JobKind.INGEST.value:
                await _set_document(session, job, DocumentStatus.PROCESSING, None)
            return ClaimedJob(
                id=job.id,
                document_id=job.document_id,
                kind=JobKind(job.kind),
                attempt=job.attempts,
                max_attempts=job.max_attempts,
                pages=job.progress_total,
            )


async def heartbeat(
    sessions: async_sessionmaker[AsyncSession],
    job_id: int,
    worker_id: str,
    *,
    done: int,
    total: int,
) -> bool:
    """Record progress and that the worker is alive. False: the job is no longer ours."""
    async with sessions.begin() as session:
        result = await session.execute(
            update(Job)
            .where(Job.id == job_id, Job.locked_by == worker_id)
            .values(heartbeat_at=func.now(), progress_done=done, progress_total=total)
            .returning(Job.id)
        )
        return result.scalar_one_or_none() is not None


async def mark_succeeded(session: AsyncSession, job_id: int, worker_id: str) -> bool:
    """In the caller's transaction, so results and completion commit together."""
    result = await session.execute(
        update(Job)
        .where(Job.id == job_id, Job.locked_by == worker_id)
        .values(
            status=JobStatus.SUCCEEDED.value,
            finished_at=func.now(),
            error=None,
            progress_done=Job.progress_total,
        )
        .returning(Job.id)
    )
    return result.scalar_one_or_none() is not None


async def mark_failed_attempt(
    sessions: async_sessionmaker[AsyncSession],
    job_id: int,
    worker_id: str,
    failure: Failure,
    *,
    backoff: timedelta,
) -> None:
    """Retry later, or give up: after the last attempt, or when retrying can't help."""
    async with sessions.begin() as session:
        job = (
            await session.execute(
                select(Job).where(Job.id == job_id, Job.locked_by == worker_id).with_for_update()
            )
        ).scalar_one_or_none()
        if job is None:  # reclaimed by another worker meanwhile: it owns the outcome
            return
        if not failure.retry or job.attempts >= job.max_attempts:
            await _mark_failed(session, job, failure)
            return
        job.status = JobStatus.QUEUED.value
        job.locked_by = None
        job.error = failure.detail
        job.run_after = func.now() + backoff * job.attempts
        if job.kind == JobKind.INGEST.value:
            await _set_document(session, job, DocumentStatus.QUEUED, None)


async def _mark_failed(session: AsyncSession, job: Job, failure: Failure) -> None:
    job.status = JobStatus.FAILED.value
    job.error = failure.detail
    job.locked_by = None
    job.finished_at = func.now()
    if job.kind == JobKind.INGEST.value:  # a failed re-chunk keeps the current chunks
        await _set_document(session, job, DocumentStatus.FAILED, failure)


async def _set_document(
    session: AsyncSession, job: Job, status: DocumentStatus, failure: Failure | None
) -> None:
    await session.execute(
        update(Document)
        .where(Document.id == job.document_id)
        .values(
            status=status.value,
            error_code=failure.code if failure else None,
            error=failure.message if failure else None,
        )
    )
