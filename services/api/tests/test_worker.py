"""The worker keeps polling through database outages and stops promptly when asked."""

import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine

from app.config import Settings
from app.worker import check_database, run_worker

UNREACHABLE_URL = "postgresql+asyncpg://app:app@127.0.0.1:1/app"


class CountingPoll:
    """Wraps the real database check and records each result."""

    def __init__(self) -> None:
        self.results: list[str | None] = []

    async def __call__(self, engine: AsyncEngine) -> str | None:
        result = await check_database(engine)
        self.results.append(result)
        return result


async def _run_until(settings: Settings, poll: CountingPoll, polls: int) -> None:
    stop = asyncio.Event()
    task = asyncio.create_task(run_worker(settings, stop, poll=poll))
    async with asyncio.timeout(20):
        while len(poll.results) < polls:
            assert not task.done(), "worker exited before it was asked to stop"
            await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=5)


@pytest.mark.anyio
async def test_worker_polls_until_asked_to_stop(database_url: str) -> None:
    poll = CountingPoll()
    settings = Settings(database_url=database_url, worker_poll_interval_seconds=0.05)

    await _run_until(settings, poll, polls=3)

    assert poll.results[:3] == [None, None, None]


@pytest.mark.anyio
async def test_worker_keeps_polling_while_the_database_is_unreachable() -> None:
    poll = CountingPoll()
    settings = Settings(
        database_url=UNREACHABLE_URL,
        db_connect_timeout_seconds=0.5,
        worker_poll_interval_seconds=0.05,
    )

    await _run_until(settings, poll, polls=2)

    assert all(result is not None for result in poll.results)
