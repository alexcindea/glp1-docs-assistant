"""Background worker: same codebase as the API, separate process.

    uv run python -m app.worker

Claims jobs from the Postgres queue (app.jobs) one at a time. An ingest job
parses the stored PDF (Docling, in a thread), chunks it, and saves everything in
one transaction together with marking the job done. A rechunk job reuses the
stored parse. While a job runs, a heartbeat records page progress; if the worker
dies, the stale heartbeat lets another worker take the job over. SIGINT/SIGTERM
stop it after the current job.

Run several workers for more throughput: claiming is safe across processes.
"""

import asyncio
import contextlib
import signal
import tempfile
import time
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from types import FrameType
from typing import Protocol

import structlog
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings, get_settings
from app.db import DATABASE_ERRORS, create_engine, create_sessionmaker, database_error_name
from app.embeddings import Embedder, Vector
from app.ingest.chunking import ChunkDraft, chunk_document
from app.ingest.failures import Failure, describe
from app.ingest.models import ParsedDocument
from app.ingest.store import chunk_search_texts, save_chunks, save_parse, stored_embeddings
from app.ingest.structure import with_sections
from app.jobs import ClaimedJob, claim_next, heartbeat, mark_failed_attempt, mark_succeeded
from app.models import Document, DocumentFile, DocumentStatus, JobKind, ParsedRecord
from app.observability import configure_logging
from app.providers import build_embedder

logger = structlog.get_logger("app.worker")  # not __main__ under `python -m`


class Parser(Protocol):
    def parse(
        self,
        path: Path,
        *,
        page_range: tuple[int, int] | None = None,
        on_progress: Callable[[int, int], None] | None = None,
    ) -> ParsedDocument: ...


ParserFactory = Callable[[], Parser]


def docling_parser_factory(settings: Settings) -> ParserFactory:
    """Imports Docling on first use: the API image doesn't have it, and doesn't need it."""
    parser: Parser | None = None

    def factory() -> Parser:
        nonlocal parser
        if parser is None:
            from app.ingest.parse import DoclingParser

            docling = DoclingParser(batch_size=settings.ingest_batch_pages)
            docling.warm_up()
            parser = docling
        return parser

    return factory


@dataclass
class PollResult:
    problem: str | None  # why the database is unusable, or None
    worked: bool  # a job was claimed (and finished or failed)


class _Progress:
    """Written by the parsing thread, read by the heartbeat: plain ints, no lock needed."""

    def __init__(self, total: int) -> None:
        self.done = 0
        self.total = total

    def update(self, done: int, total: int) -> None:
        self.done, self.total = done, total


class _JobLostError(Exception):
    """Another worker reclaimed the job while this one was still working on it."""


class IngestWorker:
    def __init__(
        self,
        settings: Settings,
        sessions: async_sessionmaker[AsyncSession],
        parser_factory: ParserFactory,
        embedder: Embedder,
    ) -> None:
        self.settings = settings
        self.sessions = sessions
        self.parser_factory = parser_factory
        self.embedder = embedder
        self.worker_id = settings.worker_id

    async def poll(self) -> PollResult:
        try:
            job = await claim_next(
                self.sessions,
                self.worker_id,
                stale_after=timedelta(seconds=self.settings.job_stale_after_seconds),
            )
        except DATABASE_ERRORS as exc:
            return PollResult(problem=database_error_name(exc), worked=False)
        if job is None:
            return PollResult(problem=None, worked=False)
        await self._run(job)
        return PollResult(problem=None, worked=True)

    async def _run(self, job: ClaimedJob) -> None:
        log = logger.bind(job_id=job.id, document_id=str(job.document_id), kind=job.kind.value)
        log.info("job.started", attempt=job.attempt, max_attempts=job.max_attempts)
        progress = _Progress(total=job.pages)
        beat = asyncio.create_task(self._heartbeat(job, progress))
        try:
            if job.kind == JobKind.INGEST:
                await self._ingest(job, progress, log)
            else:
                await self._rechunk(job, log)
            log.info("job.succeeded")
        except _JobLostError:
            log.warning("job.lost", reason="reclaimed by another worker; results discarded")
        except Exception as exc:  # every failure, whatever it is, must be recorded on the job
            failure = describe(exc)
            log.exception("job.failed", code=failure.code, retry=failure.retry)
            await self._record_failure(job, failure, log)
        finally:
            beat.cancel()
            (outcome,) = await asyncio.gather(beat, return_exceptions=True)
            if isinstance(outcome, Exception):
                log.error("job.heartbeat_crashed", error=f"{type(outcome).__name__}: {outcome}")

    async def _record_failure(
        self, job: ClaimedJob, failure: Failure, log: structlog.typing.FilteringBoundLogger
    ) -> None:
        """Best effort: if the database is down too, the stale heartbeat will hand the
        job to a worker later, so this worker must not die trying."""
        try:
            await mark_failed_attempt(
                self.sessions,
                job.id,
                self.worker_id,
                failure,
                backoff=timedelta(seconds=self.settings.job_retry_backoff_seconds),
            )
        except DATABASE_ERRORS as exc:
            log.warning("job.failure_not_recorded", reason=database_error_name(exc))

    async def _heartbeat(self, job: ClaimedJob, progress: _Progress) -> None:
        while True:
            await asyncio.sleep(self.settings.job_heartbeat_seconds)
            try:
                ours = await heartbeat(
                    self.sessions, job.id, self.worker_id, done=progress.done, total=progress.total
                )
            except DATABASE_ERRORS as exc:
                logger.warning(
                    "job.heartbeat_failed", job_id=job.id, reason=database_error_name(exc)
                )
                continue
            if not ours:
                logger.warning("job.heartbeat_lost", job_id=job.id)
                return

    async def _ingest(
        self, job: ClaimedJob, progress: _Progress, log: structlog.typing.FilteringBoundLogger
    ) -> None:
        async with self.sessions() as session:
            content = await session.scalar(
                select(DocumentFile.content).where(DocumentFile.document_id == job.document_id)
            )
        if content is None:
            raise FileNotFoundError("the document's PDF is missing from the database")

        parser = await asyncio.to_thread(self.parser_factory)
        started = time.perf_counter()
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "document.pdf"
            path.write_bytes(content)
            parsed = await asyncio.to_thread(parser.parse, path, on_progress=progress.update)
        seconds = time.perf_counter() - started
        chunks = chunk_document(parsed)
        embeddings = await self._embed(job.document_id, chunks, log)

        async with self.sessions.begin() as session:
            if not await mark_succeeded(session, job.id, self.worker_id):
                raise _JobLostError
            await save_parse(session, job.document_id, parsed)
            await save_chunks(session, job.document_id, chunks, embeddings, self.embedder.name)
            await self._mark_ready(session, job.document_id, parsed)
        pages = len(parsed.pages)
        log.info(
            "ingest.done",
            pages=pages,
            scanned_pages=sum(not p.has_text_layer for p in parsed.pages),
            blocks=len(parsed.blocks),
            chunks=len(chunks),
            seconds=round(seconds, 1),
            seconds_per_page=round(seconds / max(pages, 1), 2),
        )

    async def _rechunk(self, job: ClaimedJob, log: structlog.typing.FilteringBoundLogger) -> None:
        async with self.sessions() as session:
            stored = await session.scalar(
                select(ParsedRecord.parsed).where(ParsedRecord.document_id == job.document_id)
            )
        if stored is None:
            raise LookupError("no stored parse to re-chunk; ingest the document again")
        # Section rules may have changed since the parse: recompute them (milliseconds).
        parsed = with_sections(ParsedDocument.model_validate(stored))
        chunks = chunk_document(parsed)
        embeddings = await self._embed(job.document_id, chunks, log)
        async with self.sessions.begin() as session:
            if not await mark_succeeded(session, job.id, self.worker_id):
                raise _JobLostError
            await save_chunks(session, job.document_id, chunks, embeddings, self.embedder.name)
            # Keep the stored parse's section paths in step with the chunks'.
            await session.execute(
                update(ParsedRecord)
                .where(ParsedRecord.document_id == job.document_id)
                .values(parsed=parsed.model_dump(mode="json"))
            )
            await self._mark_ready(session, job.document_id, parsed)

    async def _embed(
        self,
        document_id: uuid.UUID,
        chunks: Sequence[ChunkDraft],
        log: structlog.typing.FilteringBoundLogger,
    ) -> list[Vector]:
        """A vector per chunk. Done before the write transaction opens, so the chunks
        are saved with their vectors in one go. Vectors already stored for the same
        indexed text and model are reused, so re-chunking only embeds what changed."""
        texts = chunk_search_texts(chunks)
        async with self.sessions() as session:
            known = await stored_embeddings(session, document_id, self.embedder.name)
        missing = list(dict.fromkeys(t for t in texts if t not in known))
        started = time.perf_counter()
        if missing:
            known.update(zip(missing, await self.embedder.embed_documents(missing), strict=True))
        log.info(
            "embed.done",
            model=self.embedder.name,
            embedded=len(missing),
            reused=len(set(texts)) - len(missing),
            seconds=round(time.perf_counter() - started, 1),
        )
        return [known[text] for text in texts]

    @staticmethod
    async def _mark_ready(
        session: AsyncSession, document_id: uuid.UUID, parsed: ParsedDocument
    ) -> None:
        await session.execute(
            update(Document)
            .where(Document.id == document_id)
            .values(
                status=DocumentStatus.READY.value,
                error=None,
                page_count=len(parsed.pages),
                scanned_pages=sum(not p.has_text_layer for p in parsed.pages),
                parser=parsed.parser,
            )
        )


async def run_worker(
    settings: Settings,
    stop: asyncio.Event,
    *,
    parser_factory: ParserFactory | None = None,
    on_poll: Callable[[PollResult], None] | None = None,
) -> None:
    engine = create_engine(settings)
    worker = IngestWorker(
        settings,
        create_sessionmaker(engine),
        parser_factory or docling_parser_factory(settings),
        build_embedder(settings),
    )
    # Host, port and database only: never log credentials.
    target = f"{engine.url.host}:{engine.url.port or 5432}/{engine.url.database}"
    logger.info("worker.started", worker_id=worker.worker_id, database=target)
    last_problem: str | None = "not checked yet"
    try:
        while not stop.is_set():
            try:
                result = await worker.poll()
            except DATABASE_ERRORS as exc:  # e.g. the database went away mid-job
                result = PollResult(problem=database_error_name(exc), worked=False)
            if on_poll:
                on_poll(result)
            if result.problem != last_problem:
                if result.problem is None:
                    logger.info("worker.database_available", database=target)
                else:
                    logger.warning(
                        "worker.database_unavailable",
                        database=target,
                        reason=result.problem,
                        retry=True,
                    )
                last_problem = result.problem
            if result.worked:
                continue  # there may be more work: don't wait
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=settings.worker_poll_interval_seconds)
    finally:
        await engine.dispose()
        logger.info("worker.stopped")


async def _main() -> None:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()

    def stop_from_loop(signum: int) -> None:
        logger.info("worker.stopping", signal=signal.Signals(signum).name)
        stop.set()

    def on_signal(signum: int, frame: FrameType | None) -> None:
        # Signal handlers can interrupt a write to stderr, so do no I/O here:
        # hand off to the event loop.
        loop.call_soon_threadsafe(stop_from_loop, signum)

    # signal.signal rather than loop.add_signal_handler: the latter is not
    # available on Windows.
    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    await run_worker(get_settings(), stop)


def main() -> None:
    configure_logging(get_settings())
    asyncio.run(_main())


if __name__ == "__main__":
    main()
