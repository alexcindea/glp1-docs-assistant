# /// script
# requires-python = "==3.12.*"
# dependencies = []
# ///
"""Download the demo corpus listed in scripts/corpus.json into corpus/.

Usage (from the repo root):
    uv run scripts/fetch_corpus.py           # download missing files, verify hashes
    uv run scripts/fetch_corpus.py --pin     # also write sha256 for entries that lack one

Idempotent: files already present with the expected hash are skipped. Every file
is checked to be a PDF and, when the manifest pins a sha256, to match it.
Standard library only, so it runs without the API project being installed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
MANIFEST = REPO_ROOT / "scripts" / "corpus.json"
CORPUS_DIR = REPO_ROOT / "corpus"

USER_AGENT = "Mozilla/5.0 (compatible; corpus-fetch/1.0)"
TIMEOUT_S = 120
ATTEMPTS = 4
CHUNK = 1 << 16
POLITE_DELAY_S = 1.0  # between downloads; EMA rate-limits bursts with HTTP 429
MAX_RETRY_AFTER_S = 60


class FetchError(Exception):
    pass


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(CHUNK):
            digest.update(chunk)
    return digest.hexdigest()


def retry_delay(exc: Exception, attempt: int) -> float | None:
    """Seconds to wait before retrying, or None if the error is permanent."""
    if isinstance(exc, urllib.error.HTTPError):
        if exc.code == 429:
            retry_after = exc.headers.get("Retry-After", "")
            if retry_after.isdigit():
                return min(float(retry_after), MAX_RETRY_AFTER_S)
        elif exc.code < 500:
            return None
    return float(2 ** (attempt + 1))


def download(url: str, dest: Path) -> None:
    """Stream url to dest via a .part file; retry rate limits and transient failures."""
    part = dest.with_suffix(dest.suffix + ".part")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    for attempt in range(1, ATTEMPTS + 1):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_S) as resp, part.open("wb") as out:
                while chunk := resp.read(CHUNK):
                    out.write(chunk)
            break
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            part.unlink(missing_ok=True)
            delay = retry_delay(exc, attempt)
            if delay is None:
                raise FetchError(f"HTTP {getattr(exc, 'code', '?')}") from exc
            if attempt == ATTEMPTS:
                raise FetchError(f"failed after {ATTEMPTS} attempts: {exc}") from exc
            time.sleep(delay)
    time.sleep(POLITE_DELAY_S)

    with part.open("rb") as f:
        if f.read(5) != b"%PDF-":
            part.unlink()
            raise FetchError("response is not a PDF")
    part.replace(dest)


def fetch_one(doc: dict[str, Any], pin: bool) -> bool:
    """Ensure one document is present and valid. Returns True if the manifest changed."""
    doc_id: str = doc["id"]
    expected: str | None = doc.get("sha256")
    dest = CORPUS_DIR / f"{doc_id}.pdf"

    if dest.exists() and expected and sha256_of(dest) == expected:
        print(f"  ok       {doc_id}")
        return False

    if not dest.exists():
        download(doc["url"], dest)
        status = "fetched"
    else:
        status = "present"

    actual = sha256_of(dest)
    size_mb = dest.stat().st_size / 1_000_000
    if expected and actual != expected:
        raise FetchError(f"sha256 mismatch: expected {expected}, got {actual}")
    if not expected:
        if pin:
            doc["sha256"] = actual
            print(f"  {status:<8} {doc_id} ({size_mb:.1f} MB) pinned {actual[:12]}")
            return True
        print(f"  {status:<8} {doc_id} ({size_mb:.1f} MB) unpinned, run with --pin")
        return False
    print(f"  {status:<8} {doc_id} ({size_mb:.1f} MB)")
    return False


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--pin", action="store_true", help="write sha256 for unpinned entries")
    args = parser.parse_args()

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    docs: list[dict[str, Any]] = manifest["documents"]
    CORPUS_DIR.mkdir(exist_ok=True)

    print(f"{len(docs)} documents -> {CORPUS_DIR}")
    started = time.perf_counter()
    changed = False
    failures: list[str] = []
    for doc in docs:
        try:
            changed |= fetch_one(doc, args.pin)
        except FetchError as exc:
            failures.append(doc["id"])
            print(f"  FAILED   {doc['id']}: {exc}")

    if changed:
        MANIFEST.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
        print(f"updated {MANIFEST.relative_to(REPO_ROOT)}")

    total_mb = sum(p.stat().st_size for p in CORPUS_DIR.glob("*.pdf")) / 1_000_000
    print(
        f"done in {time.perf_counter() - started:.0f}s, {total_mb:.0f} MB on disk, {len(failures)} failed"
    )
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
