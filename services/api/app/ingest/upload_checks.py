"""Checks an uploaded file before anything is stored. Runs in the API, without Docling.

Each rejection has a stable code the web app can act on and a message a person
can act on.
"""

import hashlib
from dataclasses import dataclass

import pypdfium2 as pdfium
import pypdfium2.raw as pdfium_raw
from fastapi import status

from app.errors import ApiError
from app.ingest.pdf import PDFIUM_LOCK

# The PDF header may sit anywhere in the first 1024 bytes (PDF 1.7, section 7.5.2).
_HEADER_WINDOW = 1024


@dataclass(frozen=True)
class CheckedPdf:
    sha256: str
    page_count: int


def check_pdf(data: bytes, *, max_pages: int) -> CheckedPdf:
    if b"%PDF-" not in data[:_HEADER_WINDOW]:
        raise ApiError(
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            "not_a_pdf",
            "The file is not a PDF. Only PDF documents can be added.",
        )
    with PDFIUM_LOCK:
        pages = _count_pages(data)
    if pages == 0:
        raise ApiError(status.HTTP_400_BAD_REQUEST, "empty_pdf", "The PDF has no pages.")
    if pages > max_pages:
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "too_many_pages",
            f"The PDF has {pages} pages; the limit is {max_pages}. Split it and upload the parts.",
        )
    return CheckedPdf(sha256=hashlib.sha256(data).hexdigest(), page_count=pages)


def _count_pages(data: bytes) -> int:
    try:
        pdf = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as exc:
        if exc.err_code == pdfium_raw.FPDF_ERR_PASSWORD:
            raise ApiError(
                status.HTTP_400_BAD_REQUEST,
                "encrypted_pdf",
                "The PDF is password-protected. Remove the password and upload it again.",
            ) from exc
        raise ApiError(
            status.HTTP_400_BAD_REQUEST,
            "unreadable_pdf",
            "The PDF is damaged or incomplete and cannot be read.",
        ) from exc
    try:
        return len(pdf)
    finally:
        pdf.close()
