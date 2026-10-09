"""Why an ingest job failed, said two ways: for people, and for the logs.

People see a stable code and a plain message on the document. The exception
text (which can contain file paths or SQL) stays in the job record and the logs.
"""

from dataclasses import dataclass


class ParseError(Exception):
    """The parser could not convert the document. Retrying won't change that."""


@dataclass(frozen=True)
class Failure:
    code: str
    message: str  # shown to users
    detail: str  # for the job record and the logs
    retry: bool  # worth another attempt?


def describe(exc: BaseException) -> Failure:
    detail = f"{type(exc).__name__}: {exc}"[:2000]
    if isinstance(exc, ParseError):
        return Failure(
            code="parse_failed",
            message="This PDF could not be read. It may be damaged or use an unusual format.",
            detail=detail,
            retry=False,
        )
    return Failure(
        code="ingest_failed",
        message="Processing this document failed. The details are in the worker logs.",
        detail=detail,
        retry=True,
    )


WORKER_CRASHED = Failure(
    code="worker_crashed",
    message="Processing stopped on every attempt, possibly because the file needs more memory.",
    detail="the worker stopped (no heartbeat) during every attempt",
    retry=False,
)
