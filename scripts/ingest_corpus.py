"""Ingest the demo corpus through the API and measure how long it takes.

Usage (from the repo root, with the API and at least one worker running):
    uv run python scripts/ingest_corpus.py --workers 2

Uploads every PDF in corpus/ (already-ingested files are recognised by their
sha256 and not uploaded twice), waits until all are ready or failed, then reads
each ingest job's start and finish times from the database. Writes
evals/results/ingest.json and evals/results/ingest.md.

Seconds per page are reported separately for documents that are all text and
documents that are all scanned, because OCR pages cost several times more.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import statistics
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from app.config import get_settings
from app.db import create_engine, create_sessionmaker
from app.models import Chunk, Document, Job, JobKind, JobStatus
from sqlalchemy import func, select

REPO_ROOT = Path(__file__).resolve().parent.parent
CORPUS = REPO_ROOT / "corpus"
RESULTS = REPO_ROOT / "evals" / "results"


def upload_all(api: str) -> dict[str, str]:
    """{document id: file name}"""
    ids = {}
    with httpx.Client(base_url=api, timeout=120) as client:
        for pdf in sorted(CORPUS.glob("*.pdf")):
            with pdf.open("rb") as handle:
                response = client.post(
                    "/documents", files={"file": (pdf.name, handle, "application/pdf")}
                )
            response.raise_for_status()
            body = response.json()
            ids[body["id"]] = pdf.name
            print(
                f"{response.status_code} {body['status']:<10} {body['page_count']:>4} p  {pdf.name}"
            )
    return ids


def wait_until_done(api: str, ids: dict[str, str]) -> None:
    started = time.monotonic()
    with httpx.Client(base_url=api, timeout=30) as client:
        while True:
            documents = [d for d in client.get("/documents").json() if d["id"] in ids]
            busy = [d for d in documents if d["status"] in ("queued", "processing")]
            pages_done = sum(
                (d["active_job"] or {}).get("pages_done", 0) if d in busy else d["page_count"]
                for d in documents
            )
            pages = sum(d["page_count"] for d in documents)
            print(
                f"{time.monotonic() - started:7.0f} s  {pages_done}/{pages} pages, "
                f"{len(busy)} documents still busy",
                flush=True,
            )
            if not busy:
                return
            time.sleep(15)


async def collect(ids: dict[str, str]) -> list[dict[str, Any]]:
    engine = create_engine(get_settings())
    sessions = create_sessionmaker(engine)
    rows = []
    async with sessions() as session:
        for document_id, name in ids.items():
            document = await session.get(Document, document_id)
            assert document is not None
            job = await session.scalar(
                select(Job)
                .where(Job.document_id == document.id, Job.kind == JobKind.INGEST.value)
                .order_by(Job.created_at.desc())
            )
            chunks = await session.scalar(
                select(func.count()).select_from(Chunk).where(Chunk.document_id == document.id)
            )
            seconds = None
            if (
                job
                and job.status == JobStatus.SUCCEEDED.value
                and job.started_at
                and job.finished_at
            ):
                seconds = (job.finished_at - job.started_at).total_seconds()
            rows.append(
                {
                    "file": name,
                    "status": document.status,
                    "error_code": document.error_code,
                    "pages": document.page_count,
                    "scanned_pages": document.scanned_pages,
                    "chunks": chunks,
                    "attempts": job.attempts if job else None,
                    "seconds": round(seconds, 1) if seconds is not None else None,
                    "seconds_per_page": round(seconds / document.page_count, 2)
                    if seconds is not None
                    else None,
                    "started_at": job.started_at.isoformat() if job and job.started_at else None,
                    "finished_at": job.finished_at.isoformat() if job and job.finished_at else None,
                }
            )
    await engine.dispose()
    return rows


def summarise(rows: list[dict[str, Any]], workers: int) -> dict[str, Any]:
    done = [r for r in rows if r["seconds"] is not None]
    text_only = [r["seconds_per_page"] for r in done if r["scanned_pages"] == 0]
    scanned_only = [r["seconds_per_page"] for r in done if r["scanned_pages"] == r["pages"]]
    starts = [datetime.fromisoformat(r["started_at"]) for r in done]
    ends = [datetime.fromisoformat(r["finished_at"]) for r in done]
    return {
        "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "workers": workers,
        "documents": len(rows),
        "failed": sum(r["status"] != "ready" for r in rows),
        "pages": sum(r["pages"] for r in rows),
        "scanned_pages": sum(r["scanned_pages"] or 0 for r in rows),
        "chunks": sum(r["chunks"] or 0 for r in rows),
        "wall_clock_seconds": round((max(ends) - min(starts)).total_seconds()) if done else None,
        "text_documents_seconds_per_page_median": round(statistics.median(text_only), 2)
        if text_only
        else None,
        "scanned_documents_seconds_per_page_median": round(statistics.median(scanned_only), 2)
        if scanned_only
        else None,
        "worker_seconds_total": round(sum(r["seconds"] for r in done)),
    }


def write(rows: list[dict[str, Any]], summary: dict[str, Any]) -> None:
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "ingest.json").write_text(
        json.dumps({"summary": summary, "documents": rows}, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    lines = [
        "# Ingest measurement",
        "",
        "Generated by `scripts/ingest_corpus.py`; times are from the job table.",
        "",
        *(f"- **{key.replace('_', ' ')}:** {value}" for key, value in summary.items()),
        "",
        "| file | pages | scanned | chunks | seconds | s/page | status |",
        "|---|---:|---:|---:|---:|---:|---|",
        *(
            f"| {r['file']} | {r['pages']} | {r['scanned_pages']} | {r['chunks']} | "
            f"{r['seconds']} | {r['seconds_per_page']} | {r['status']} |"
            for r in sorted(rows, key=lambda r: r["file"])
        ),
        "",
    ]
    (RESULTS / "ingest.md").write_text("\n".join(lines), encoding="utf-8", newline="\n")


def rechunk_all(api: str, workers: int) -> None:
    """Re-chunk every ingested corpus document from its stored parse, and time it."""
    names = {pdf.name for pdf in CORPUS.glob("*.pdf")}
    with httpx.Client(base_url=api, timeout=30) as client:
        documents = [
            d
            for d in client.get("/documents").json()
            if d["filename"] in names and d["status"] == "ready"
        ]
        started = time.monotonic()
        for document in documents:
            client.post(f"/documents/{document['id']}/rechunk").raise_for_status()
        while any(
            d["active_job"]
            for d in client.get("/documents").json()
            if d["id"] in {x["id"] for x in documents}
        ):
            time.sleep(1)
        wall = time.monotonic() - started
    summary = asyncio.run(_chunk_stats([d["id"] for d in documents]))
    summary.update(
        {
            "measured_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "workers": workers,
            "documents": len(documents),
            "pages": sum(d["page_count"] for d in documents),
            "rechunk_wall_clock_seconds": round(wall, 1),
        }
    )
    RESULTS.mkdir(parents=True, exist_ok=True)
    (RESULTS / "rechunk.json").write_text(
        json.dumps(summary, indent=2) + "\n", encoding="utf-8", newline="\n"
    )
    print(json.dumps(summary, indent=2))


async def _chunk_stats(ids: list[str]) -> dict[str, Any]:
    engine = create_engine(get_settings())
    async with create_sessionmaker(engine)() as session:
        rows = (
            await session.execute(
                select(Chunk.kind, Chunk.token_count, Chunk.chunker).where(
                    Chunk.document_id.in_(ids)
                )
            )
        ).all()
    await engine.dispose()
    tokens = sorted(t for kind, t, _ in rows if kind == "text")
    tables = sorted(t for kind, t, _ in rows if kind == "table")
    return {
        "chunker": sorted({c for _, _, c in rows}),
        "chunks": len(rows),
        "text_chunks": len(tokens),
        "table_chunks": len(tables),
        "text_tokens_median": statistics.median(tokens) if tokens else None,
        "text_tokens_p95": tokens[int(len(tokens) * 0.95)] if tokens else None,
        "text_tokens_max": tokens[-1] if tokens else None,
        "table_tokens_max": tables[-1] if tables else None,
        "chunks_over_450_tokens": sum(t > 450 for _, t, _ in rows),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--api", default="http://127.0.0.1:8000")
    parser.add_argument(
        "--workers", type=int, required=True, help="how many workers are running (recorded)"
    )
    parser.add_argument(
        "--rechunk",
        action="store_true",
        help="re-chunk the already-ingested corpus instead (writes evals/results/rechunk.json)",
    )
    args = parser.parse_args()
    if args.rechunk:
        rechunk_all(args.api, args.workers)
        return

    ids = upload_all(args.api)
    wait_until_done(args.api, ids)
    rows = asyncio.run(collect(ids))
    summary = summarise(rows, args.workers)
    write(rows, summary)
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
