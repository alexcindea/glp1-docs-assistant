"""Background worker: same codebase as the API, separate process.

Run with `python -m app.worker`. It polls Postgres on a fixed interval. Ingestion
jobs (claimed with FOR UPDATE SKIP LOCKED) plug into the poll with ingestion;
until then a poll only checks the database, and the worker logs when the
database goes away (with the reason) or comes back. SIGINT/SIGTERM stop it after
the current poll.
"""

import asyncio
import contextlib
import signal
from collections.abc import Awaitable, Callable
from types import FrameType

import structlog
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings, get_settings
from app.db import DATABASE_ERRORS, create_engine, database_error_name
from app.observability import configure_logging

logger = structlog.get_logger("app.worker")  # not __main__ under `python -m`

# Returns None when the database is usable, otherwise why it is not.
Poll = Callable[[AsyncEngine], Awaitable[str | None]]


async def check_database(engine: AsyncEngine) -> str | None:
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except DATABASE_ERRORS as exc:
        return database_error_name(exc)
    return None


async def run_worker(settings: Settings, stop: asyncio.Event, poll: Poll = check_database) -> None:
    engine = create_engine(settings)
    # Host, port and database only: never log credentials.
    target = f"{engine.url.host}:{engine.url.port or 5432}/{engine.url.database}"
    logger.info(
        "worker.started",
        database=target,
        poll_interval_seconds=settings.worker_poll_interval_seconds,
    )
    last_problem: str | None = "not checked yet"
    try:
        while not stop.is_set():
            problem = await poll(engine)
            if problem != last_problem:
                if problem is None:
                    logger.info("worker.database_available", database=target)
                else:
                    logger.warning(
                        "worker.database_unavailable", database=target, reason=problem, retry=True
                    )
                last_problem = problem
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
