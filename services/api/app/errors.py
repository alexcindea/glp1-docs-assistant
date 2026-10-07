"""One error shape for every failure. Clients never see a stack trace."""

import logging
from collections.abc import Mapping
from http import HTTPStatus
from typing import Any

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException

logger = logging.getLogger(__name__)


class ErrorResponse(BaseModel):
    code: str
    message: str
    details: list[str] = []


# Added to every route in the OpenAPI schema, replacing FastAPI's default 422 model,
# so the generated frontend types know the error shape.
ERROR_RESPONSES: dict[int | str, dict[str, Any]] = {
    status.HTTP_422_UNPROCESSABLE_CONTENT: {"model": ErrorResponse},
    status.HTTP_500_INTERNAL_SERVER_ERROR: {"model": ErrorResponse},
}


def _error(
    status_code: int, body: ErrorResponse, headers: Mapping[str, str] | None = None
) -> JSONResponse:
    return JSONResponse(status_code=status_code, content=body.model_dump(), headers=headers)


async def _http_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, HTTPException):  # registered for HTTPException only
        return await _unhandled(request, exc)
    code = HTTPStatus(exc.status_code).phrase.lower().replace(" ", "_")
    return _error(exc.status_code, ErrorResponse(code=code, message=str(exc.detail)), exc.headers)


async def _validation_error(request: Request, exc: Exception) -> JSONResponse:
    if not isinstance(exc, RequestValidationError):  # registered for it only
        return await _unhandled(request, exc)
    details = [
        f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}" for err in exc.errors()
    ]
    body = ErrorResponse(
        code="invalid_request", message="The request is not valid.", details=details
    )
    return _error(status.HTTP_422_UNPROCESSABLE_CONTENT, body)


async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    body = ErrorResponse(
        code="internal_error",
        message="Unexpected server error. The details are in the API logs.",
    )
    return _error(status.HTTP_500_INTERNAL_SERVER_ERROR, body)


def register_error_handlers(app: FastAPI) -> None:
    app.add_exception_handler(HTTPException, _http_error)
    app.add_exception_handler(RequestValidationError, _validation_error)
    app.add_exception_handler(Exception, _unhandled)
