"""Process-wide resources created at startup, exposed to routes as typed dependencies."""

import asyncio
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from app.config import Settings
from app.embeddings import Embedder
from app.llm import LLMProvider


@dataclass(frozen=True)
class AppState:
    settings: Settings
    engine: AsyncEngine
    sessions: async_sessionmaker[AsyncSession]
    expected_revision: str | None
    llm: LLMProvider
    embedder: Embedder
    # Loading the local embedding model in the background; None for the fake.
    embedder_loading: asyncio.Task[None] | None = None


def get_app_state(request: Request) -> AppState:
    return cast(AppState, request.app.state.resources)


AppStateDep = Annotated[AppState, Depends(get_app_state)]


async def get_session(state: AppStateDep) -> AsyncIterator[AsyncSession]:
    async with state.sessions() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]
