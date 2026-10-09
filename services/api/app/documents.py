"""Documents: upload, status, and the stored chunks with their provenance.

Uploading checks the file, stores it and queues an ingest job in one transaction;
the worker does the parsing. Uploading the same bytes again returns the existing
document (matched by sha256) instead of ingesting a second copy; a document that
failed before is queued again.
"""

import uuid
from datetime import datetime
from typing import Annotated, Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Header, Response, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload
from starlette.concurrency import run_in_threadpool
from starlette.types import ASGIApp, Receive, Scope, Send

from app.errors import ApiError, ErrorResponse, error_responses
from app.ingest.upload_checks import check_pdf
from app.jobs import enqueue
from app.models import (
    Chunk,
    Document,
    DocumentFile,
    DocumentStatus,
    Job,
    JobKind,
    JobStatus,
)
from app.state import AppStateDep, SessionDep

router = APIRouter(prefix="/documents", tags=["documents"])

# Multipart framing around the file: boundaries and part headers.
_MULTIPART_OVERHEAD = 64 * 1024


class ActiveJob(BaseModel):
    kind: JobKind
    status: Literal["queued", "running"]
    pages_done: int
    pages_total: int


class DocumentOut(BaseModel):
    id: uuid.UUID
    filename: str
    size_bytes: int
    page_count: int
    scanned_pages: int | None
    status: DocumentStatus
    error_code: str | None  # set when status is "failed"
    error: str | None  # a message for people, never exception text
    parser: str | None
    created_at: datetime
    updated_at: datetime
    active_job: ActiveJob | None  # an ingest or re-chunk that is queued or running


class SpanOut(BaseModel):
    """A range of the chunk's text and where it sits: page, box (0-1, top-left origin)."""

    page: int
    x0: float
    y0: float
    x1: float
    y1: float
    char_start: int
    char_end: int


class ChunkOut(BaseModel):
    id: int
    seq: int
    kind: Literal["text", "table"]
    text: str
    section_path: list[str]
    token_count: int
    spans: list[SpanOut]


class UploadSizeLimit:
    """Rejects an upload whose declared size is too big before reading the body.

    Without it, the whole request is spooled to disk before the route can say no.
    Requests without Content-Length are still capped by the route; a proxy in
    front should enforce its own limit too.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"] == "/documents":
            declared = dict(scope["headers"]).get(b"content-length")
            if declared and declared.isdigit() and int(declared) > self.max_bytes:
                body = ErrorResponse(code="too_large", message=_too_large(self.max_bytes))
                payload = body.model_dump_json().encode()
                await send(
                    {
                        "type": "http.response.start",
                        "status": status.HTTP_413_CONTENT_TOO_LARGE,
                        "headers": [
                            (b"content-type", b"application/json"),
                            (b"content-length", str(len(payload)).encode()),
                            (b"connection", b"close"),
                        ],
                    }
                )
                await send({"type": "http.response.body", "body": payload})
                return
        await self.app(scope, receive, send)


def upload_limit_bytes(max_upload_mb: int) -> int:
    return max_upload_mb * 1024 * 1024 + _MULTIPART_OVERHEAD


def _too_large(max_bytes: int) -> str:
    return f"The file is larger than {max_bytes // (1024 * 1024)} MB."


async def _active_jobs(session: SessionDep, ids: list[uuid.UUID]) -> dict[uuid.UUID, Job]:
    if not ids:
        return {}
    jobs = await session.scalars(
        select(Job).where(
            Job.document_id.in_(ids),
            Job.status.in_([JobStatus.QUEUED.value, JobStatus.RUNNING.value]),
        )
    )
    return {job.document_id: job for job in jobs}


def _out(document: Document, job: Job | None) -> DocumentOut:
    active = None
    if job is not None:
        active = ActiveJob(
            kind=JobKind(job.kind),
            status="running" if job.status == JobStatus.RUNNING.value else "queued",
            pages_done=job.progress_done,
            pages_total=job.progress_total,
        )
    fields = {
        name: getattr(document, name) for name in DocumentOut.model_fields if name != "active_job"
    }
    return DocumentOut.model_validate({**fields, "active_job": active})


async def _get_document(
    session: SessionDep, document_id: uuid.UUID, *, for_update: bool = False
) -> Document:
    query = select(Document).where(Document.id == document_id)
    document = await session.scalar(query.with_for_update() if for_update else query)
    if document is None:
        raise ApiError(status.HTTP_404_NOT_FOUND, "not_found", "No document with this id.")
    return document


async def _document_out(session: SessionDep, document: Document) -> DocumentOut:
    jobs = await _active_jobs(session, [document.id])
    return _out(document, jobs.get(document.id))


@router.post(
    "",
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        status.HTTP_200_OK: {
            "model": DocumentOut,
            "description": "Already uploaded: the existing document",
        },
        **error_responses(400, 409, 413, 415),
    },
)
async def upload_document(
    file: Annotated[UploadFile, File(description="A PDF")],
    state: AppStateDep,
    session: SessionDep,
    response: Response,
) -> DocumentOut:
    """Check and store a PDF, and queue it for ingestion (202)."""
    limit = state.settings.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise ApiError(status.HTTP_413_CONTENT_TOO_LARGE, "too_large", _too_large(limit))
    checked = await run_in_threadpool(check_pdf, data, max_pages=state.settings.max_pages)

    document = await session.scalar(select(Document).where(Document.sha256 == checked.sha256))
    if document is None:
        document = Document(
            sha256=checked.sha256,
            filename=file.filename or "document.pdf",
            size_bytes=len(data),
            page_count=checked.page_count,
            file=DocumentFile(content=data),
        )
        session.add(document)
    elif document.status != DocumentStatus.FAILED.value:
        response.status_code = status.HTTP_200_OK
        return await _document_out(session, document)
    else:  # it failed before: try again
        document.status = DocumentStatus.QUEUED.value
        document.error_code = document.error = None

    try:
        await session.flush()  # assigns the id, or hits the sha256 / one-active-job rules
        job = enqueue(
            session,
            document.id,
            JobKind.INGEST,
            max_attempts=state.settings.job_max_attempts,
            pages=document.page_count,
        )
        await session.commit()
    except IntegrityError:
        # The same file was uploaded at the same moment and that request won.
        await session.rollback()
        winner = await session.scalar(select(Document).where(Document.sha256 == checked.sha256))
        if winner is None:
            raise ApiError(
                status.HTTP_409_CONFLICT,
                "upload_conflict",
                "The same file is being uploaded right now. Try again in a moment.",
            ) from None
        response.status_code = status.HTTP_200_OK
        return await _document_out(session, winner)
    await session.refresh(document)
    return _out(document, job)


@router.get("")
async def list_documents(session: SessionDep) -> list[DocumentOut]:
    documents = list(await session.scalars(select(Document).order_by(Document.created_at.desc())))
    jobs = await _active_jobs(session, [d.id for d in documents])
    return [_out(d, jobs.get(d.id)) for d in documents]


@router.get("/{document_id}", responses=error_responses(404))
async def get_document(document_id: uuid.UUID, session: SessionDep) -> DocumentOut:
    return await _document_out(session, await _get_document(session, document_id))


@router.get("/{document_id}/chunks", responses=error_responses(404))
async def list_chunks(document_id: uuid.UUID, session: SessionDep) -> list[ChunkOut]:
    """The document's chunks in reading order, each with the spans that locate its text."""
    await _get_document(session, document_id)
    chunks = await session.scalars(
        select(Chunk)
        .where(Chunk.document_id == document_id)
        .order_by(Chunk.seq)
        .options(selectinload(Chunk.spans))
    )
    return [ChunkOut.model_validate(chunk, from_attributes=True) for chunk in chunks]


@router.get(
    "/{document_id}/file",
    response_class=Response,
    responses={
        200: {
            "content": {"application/pdf": {"schema": {"type": "string", "format": "binary"}}},
            "description": "The PDF as uploaded.",
        },
        304: {"description": "The client's copy is current."},
        **error_responses(404),
    },
)
async def get_file(
    document_id: uuid.UUID,
    session: SessionDep,
    if_none_match: Annotated[str | None, Header()] = None,
) -> Response:
    """The original PDF, for the viewer. Its bytes never change (a document is its sha256),
    so clients may keep it for good; the sha256 is its ETag."""
    document = await _get_document(session, document_id)
    etag = f'"{document.sha256}"'
    headers = {"ETag": etag, "Cache-Control": "private, max-age=31536000, immutable"}
    if if_none_match == etag:
        return Response(status_code=status.HTTP_304_NOT_MODIFIED, headers=headers)
    content = await session.scalar(
        select(DocumentFile.content).where(DocumentFile.document_id == document_id)
    )
    if content is None:  # every document is stored with its file; this is a broken row
        raise ApiError(status.HTTP_404_NOT_FOUND, "file_missing", "This document has no file.")
    headers["Content-Disposition"] = f"inline; filename*=UTF-8''{quote(document.filename)}"
    return Response(content=content, media_type="application/pdf", headers=headers)


@router.post(
    "/{document_id}/rechunk",
    status_code=status.HTTP_202_ACCEPTED,
    responses=error_responses(404, 409),
)
async def rechunk_document(
    document_id: uuid.UUID, state: AppStateDep, session: SessionDep
) -> DocumentOut:
    """Chunk again from the stored parse (no OCR): seconds, not minutes.

    The document stays `ready` with its current chunks until the new ones replace them.
    """
    document = await _get_document(session, document_id, for_update=True)
    if document.status != DocumentStatus.READY.value:
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "not_ready",
            "Only a document that finished ingesting can be re-chunked.",
        )
    try:
        job = enqueue(
            session,
            document.id,
            JobKind.RECHUNK,
            max_attempts=state.settings.job_max_attempts,
            pages=0,
        )
        await session.commit()
    except IntegrityError:
        await session.rollback()
        raise ApiError(
            status.HTTP_409_CONFLICT,
            "already_processing",
            "This document is already being processed. Try again when it finishes.",
        ) from None
    return _out(document, job)
