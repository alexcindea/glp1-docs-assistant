"""The worker loop keeps polling through database outages and stops promptly when asked."""

import asyncio

import pytest

from app.config import Settings
from app.worker import PollResult, run_worker
from tests.factories import FakeParser

UNREACHABLE_URL = "postgresql+asyncpg://app:app@127.0.0.1:1/app"


async def _run_until(settings: Settings, polls: int) -> list[PollResult]:
    results: list[PollResult] = []
    stop = asyncio.Event()
    task = asyncio.create_task(
        run_worker(settings, stop, parser_factory=FakeParser, on_poll=results.append)
    )
    async with asyncio.timeout(20):
        while len(results) < polls:
            assert not task.done(), "worker exited before it was asked to stop"
            await asyncio.sleep(0.02)
    stop.set()
    await asyncio.wait_for(task, timeout=5)
    return results


@pytest.mark.anyio
async def test_idle_worker_polls_until_asked_to_stop(migrated_database_url: str) -> None:
    settings = Settings(database_url=migrated_database_url, worker_poll_interval_seconds=0.05)

    results = await _run_until(settings, polls=3)

    assert all(r == PollResult(problem=None, worked=False) for r in results[:3])


@pytest.mark.anyio
async def test_worker_keeps_polling_while_the_database_is_unreachable() -> None:
    settings = Settings(
        database_url=UNREACHABLE_URL,
        db_connect_timeout_seconds=0.5,
        worker_poll_interval_seconds=0.05,
    )

    results = await _run_until(settings, polls=2)

    assert all(r.problem is not None and not r.worked for r in results)


@pytest.mark.anyio
async def test_worker_reports_rejected_connections_and_keeps_polling(
    rejected_database_url: tuple[str, str],
) -> None:
    url, error = rejected_database_url
    settings = Settings(database_url=url, worker_poll_interval_seconds=0.05)

    results = await _run_until(settings, polls=2)

    assert [r.problem for r in results[:2]] == [error, error]
