"""PDF -> ParsedDocument, with Docling. Worker only (needs the `ingest` extra).

    uv run python -m app.ingest.parse corpus/file.pdf [--pages 3-8] [--out parsed.json]

OCR (RapidOCR) runs only on pages without a usable text layer: pages are grouped
into runs of scanned / not scanned, and each run goes to a converter with OCR on
or off. Runs are converted in batches, which bounds memory on long reports and
lets the worker report progress. Docling only joins a paragraph that continues
onto the next page when both pages are in the same batch; across a batch
boundary it stays two blocks, which costs some context but never provenance.
"""

import argparse
import importlib.metadata
import itertools
import time
from collections.abc import Callable, Iterator
from pathlib import Path

from docling.datamodel.base_models import ConversionStatus, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions, RapidOcrOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.types.doc.base import CoordOrigin
from docling_core.types.doc.document import DoclingDocument
from docling_core.types.doc.items.node import DocItem
from docling_core.types.doc.items.table.table import TableItem
from docling_core.types.doc.items.text import TextItem
from docling_core.types.doc.labels import DocItemLabel

from app.ingest.failures import ParseError
from app.ingest.models import (
    Block,
    BlockKind,
    Page,
    ParsedDocument,
    Region,
    Table,
    TableCell,
)
from app.ingest.pdf import inspect_pages
from app.ingest.structure import assign_sections, contiguous_ranges, normalize_bbox

_KINDS: dict[DocItemLabel, BlockKind] = {
    DocItemLabel.TITLE: BlockKind.TITLE,
    DocItemLabel.SECTION_HEADER: BlockKind.HEADING,
    DocItemLabel.TEXT: BlockKind.PARAGRAPH,
    DocItemLabel.PARAGRAPH: BlockKind.PARAGRAPH,
    DocItemLabel.REFERENCE: BlockKind.PARAGRAPH,
    DocItemLabel.HANDWRITTEN_TEXT: BlockKind.PARAGRAPH,
    DocItemLabel.LIST_ITEM: BlockKind.LIST_ITEM,
    DocItemLabel.TABLE: BlockKind.TABLE,
    DocItemLabel.CAPTION: BlockKind.CAPTION,
    DocItemLabel.FOOTNOTE: BlockKind.FOOTNOTE,
    DocItemLabel.FORMULA: BlockKind.FORMULA,
    DocItemLabel.CODE: BlockKind.CODE,
    DocItemLabel.DOCUMENT_INDEX: BlockKind.INDEX,
}
# Pictures, charts and form widgets carry no text we can cite, so they are dropped.

ProgressCallback = Callable[[int, int], None]  # (pages done, pages total)


def parser_version() -> str:
    versions = (f"{p} {importlib.metadata.version(p)}" for p in ("docling", "rapidocr"))
    return " / ".join(versions)


def _converter(*, ocr: bool) -> DocumentConverter:
    options = PdfPipelineOptions(
        do_ocr=ocr,
        ocr_options=RapidOcrOptions(),
        do_table_structure=True,
    )
    return DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=options)}
    )


class DoclingParser:
    """Holds the converters: loading models costs seconds, so build them once per process."""

    def __init__(self, batch_size: int = 10) -> None:
        self.batch_size = batch_size
        self._converters = {True: _converter(ocr=True), False: _converter(ocr=False)}

    def warm_up(self) -> None:
        """Load the models now instead of on the first document."""
        for converter in self._converters.values():
            converter.initialize_pipeline(InputFormat.PDF)

    def parse(
        self,
        path: Path,
        *,
        page_range: tuple[int, int] | None = None,
        on_progress: ProgressCallback | None = None,
    ) -> ParsedDocument:
        pages = inspect_pages(path)
        first, last = page_range or (1, len(pages))
        if not 1 <= first <= last <= len(pages):
            raise ValueError(f"page range {first}-{last} is outside 1-{len(pages)}")
        selected = [p for p in pages if first <= p.number <= last]

        drafts: list[Block] = []
        done = 0
        for start, end, scanned in self._batches(selected):
            result = self._converters[scanned].convert(
                path, page_range=(start, end), raises_on_error=False
            )
            if result.status not in (ConversionStatus.SUCCESS, ConversionStatus.PARTIAL_SUCCESS):
                details = "; ".join(e.error_message for e in result.errors) or result.status
                raise ParseError(f"pages {start}-{end}: {details}")
            drafts.extend(_blocks(result.document, first_index=len(drafts)))
            done += end - start + 1
            if on_progress:
                on_progress(done, len(selected))

        headings, paths = assign_sections(
            [b.kind in (BlockKind.HEADING, BlockKind.TITLE) for b in drafts],
            [b.text for b in drafts],
        )
        blocks = [
            b.model_copy(
                update={
                    "heading_depth": heading.depth if heading else None,
                    "section_path": path,
                }
            )
            for b, heading, path in zip(drafts, headings, paths, strict=True)
        ]
        return ParsedDocument(pages=selected, blocks=blocks, parser=parser_version())

    def _batches(self, pages: list[Page]) -> Iterator[tuple[int, int, bool]]:
        """(first page, last page, scanned) per batch: runs of like pages, cut to size."""
        for scanned, run in itertools.groupby(pages, key=lambda p: not p.has_text_layer):
            numbers = [p.number for p in run]
            for i in range(0, len(numbers), self.batch_size):
                batch = numbers[i : i + self.batch_size]
                yield batch[0], batch[-1], scanned


def _blocks(doc: DoclingDocument, first_index: int) -> list[Block]:
    blocks: list[Block] = []
    for item, _level in doc.iterate_items():
        if not isinstance(item, DocItem) or item.label not in _KINDS or not item.prov:
            continue
        kind = _KINDS[item.label]
        table: Table | None = None
        if isinstance(item, TableItem):
            text = _table_markdown(item, doc)
            table = _table(item, doc)
        elif isinstance(item, TextItem):
            # Formulas keep their text in `orig` unless formula enrichment is on.
            text = item.text or item.orig
        else:
            continue
        if not text.strip():
            continue
        blocks.append(
            Block(
                index=first_index + len(blocks),
                kind=kind,
                text=text,
                regions=_regions(item, doc, text, table=isinstance(item, TableItem)),
                table=table,
            )
        )
    return blocks


def _regions(item: DocItem, doc: DoclingDocument, text: str, *, table: bool) -> list[Region]:
    """One region per page the item appears on, with the part of its text on that page.

    Docling flips every piece of a block that continues across pages into
    bottom-left coordinates using the *first* page's height (docling 2.135,
    ReadingOrderModel._merge_elements), so that height is used to flip them back.
    A table's Markdown can't be split by page, so each of its regions spans all of it.
    """
    first_height = doc.pages[item.prov[0].page_no].size.height
    if table:
        ranges = [(0, len(text))] * len(item.prov)
    else:
        ranges = contiguous_ranges(text, [p.charspan for p in item.prov])
    regions = []
    for prov, (start, end) in zip(item.prov, ranges, strict=True):
        size = doc.pages[prov.page_no].size
        bbox = normalize_bbox(
            prov.bbox.l,
            prov.bbox.t,
            prov.bbox.r,
            prov.bbox.b,
            page_width=size.width,
            page_height=size.height,
            origin_bottom_left=prov.bbox.coord_origin == CoordOrigin.BOTTOMLEFT,
            flip_height=first_height,
        )
        regions.append(Region(page=prov.page_no, bbox=bbox, char_start=start, char_end=end))
    return regions


def _table_markdown(item: TableItem, doc: DoclingDocument) -> str:
    """The table as Markdown, without its caption (the caption is its own block)."""
    markdown = item.export_to_markdown(doc=doc).strip()
    caption = item.caption_text(doc).strip()
    if caption and markdown.startswith(caption):
        markdown = markdown[len(caption) :].lstrip()
    return markdown


def _table(item: TableItem, doc: DoclingDocument) -> Table:
    size = doc.pages[item.prov[0].page_no].size
    cells = []
    for cell in item.data.table_cells:
        bbox = None
        if cell.bbox is not None:
            bbox = normalize_bbox(
                cell.bbox.l,
                cell.bbox.t,
                cell.bbox.r,
                cell.bbox.b,
                page_width=size.width,
                page_height=size.height,
                origin_bottom_left=cell.bbox.coord_origin == CoordOrigin.BOTTOMLEFT,
            )
        cells.append(
            TableCell(
                row=cell.start_row_offset_idx,
                col=cell.start_col_offset_idx,
                row_span=max(cell.row_span, 1),
                col_span=max(cell.col_span, 1),
                text=cell.text,
                is_header=cell.column_header or cell.row_header,
                bbox=bbox,
            )
        )
    return Table(n_rows=item.data.num_rows, n_cols=item.data.num_cols, cells=cells)


def main() -> None:
    parser = argparse.ArgumentParser(description="Parse a PDF into blocks with provenance.")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--pages", help="page range, e.g. 3-8 (1-based, inclusive)")
    parser.add_argument("--out", type=Path, help="write the ParsedDocument as JSON here")
    args = parser.parse_args()

    page_range = None
    if args.pages:
        first, _, last = args.pages.partition("-")
        page_range = (int(first), int(last or first))

    docling = DoclingParser()
    started = time.perf_counter()
    docling.warm_up()
    loaded = time.perf_counter()
    parsed = docling.parse(args.pdf, page_range=page_range)
    seconds = time.perf_counter() - loaded

    kinds: dict[str, int] = {}
    for block in parsed.blocks:
        kinds[block.kind.value] = kinds.get(block.kind.value, 0) + 1
    scanned = sum(not p.has_text_layer for p in parsed.pages)
    print(
        f"{args.pdf.name}: {len(parsed.pages)} pages ({scanned} scanned), "
        f"{len(parsed.blocks)} blocks {kinds}; models {loaded - started:.1f} s, "
        f"parse {seconds:.1f} s = {seconds / max(len(parsed.pages), 1):.2f} s/page"
    )
    if args.out:
        args.out.write_text(parsed.model_dump_json(indent=1), encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
