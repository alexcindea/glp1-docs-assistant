"""Facts about a PDF that don't need Docling: page sizes and which pages have text.

Used by the API (upload checks) and the worker (which pages were scanned).
"""

from pathlib import Path

import pypdfium2 as pdfium

from app.ingest.models import Page

# Fewer real characters than this and the page counts as scanned: a page number
# or a stamp is not a usable text layer.
MIN_TEXT_CHARS = 20


def inspect_pages(path: Path) -> list[Page]:
    pdf = pdfium.PdfDocument(path)
    try:
        pages = []
        for index in range(len(pdf)):
            page = pdf[index]
            width, height = page.get_size()
            textpage = page.get_textpage()
            text = textpage.get_text_range()
            textpage.close()
            page.close()
            chars = sum(1 for ch in text if not ch.isspace())
            pages.append(
                Page(
                    number=index + 1,
                    width_pt=width,
                    height_pt=height,
                    has_text_layer=chars >= MIN_TEXT_CHARS,
                )
            )
        return pages
    finally:
        pdf.close()
