"""Process-wide resources created at startup, exposed to routes as a typed dependency."""

from dataclasses import dataclass
from typing import Annotated, cast

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncEngine


@dataclass(frozen=True)
class AppState:
    engine: AsyncEngine
    expected_revision: str | None


def get_app_state(request: Request) -> AppState:
    return cast(AppState, request.app.state.resources)


AppStateDep = Annotated[AppState, Depends(get_app_state)]
