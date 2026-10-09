"""Docling end to end on a two-page fixture (public-domain FDA pages):

page 1: Mounjaro label 2026, PDF page 3 (text layer, with a small table)
page 2: Byetta medical review 2005, PDF page 5 (scanned, so OCR)

Needs the `ingest` extra and downloads the models on first run.
"""

from pathlib import Path

import pytest

pytest.importorskip("docling", reason="needs the ingest extra: uv sync --all-extras")

from app.ingest.chunking import ChunkingConfig, chunk_document
from app.ingest.models import BlockKind, ParsedDocument
from app.ingest.parse import DoclingParser

pytestmark = pytest.mark.ingest

FIXTURE = Path(__file__).parent / "fixtures" / "two-pages.pdf"


@pytest.fixture(scope="module")
def parsed() -> ParsedDocument:
    return DoclingParser(batch_size=1).parse(FIXTURE)


def _block_containing(parsed: ParsedDocument, words: str) -> int:
    matches = [b.index for b in parsed.blocks if words in " ".join(b.text.split())]
    assert matches, f"no block contains {words!r}"
    return matches[0]


def test_pages_know_whether_they_were_scanned(parsed: ParsedDocument) -> None:
    assert [p.number for p in parsed.pages] == [1, 2]
    assert [p.has_text_layer for p in parsed.pages] == [True, False]


def test_blocks_are_numbered_in_reading_order(parsed: ParsedDocument) -> None:
    assert [b.index for b in parsed.blocks] == list(range(len(parsed.blocks)))


def test_every_block_has_provenance_inside_its_text(parsed: ParsedDocument) -> None:
    for block in parsed.blocks:
        for region in block.regions:
            assert region.page in (1, 2)
            assert 0 <= region.char_start <= region.char_end <= len(block.text)


def test_text_layer_page_is_read(parsed: ParsedDocument) -> None:
    block = parsed.blocks[_block_containing(parsed, "2.5 mg increments")]
    assert block.regions[0].page == 1


def test_scanned_page_is_read_by_ocr_with_its_section(parsed: ParsedDocument) -> None:
    block = parsed.blocks[_block_containing(parsed, "develop anti-exenatide antibodies")]
    assert block.regions[0].page == 2
    assert "1.2.1 Risk Management Activity" in block.section_path
    assert block.section_path[-1] == "Identified Risks"
    # The paragraph sits in the left part of the page's middle: a sanity check on the box.
    box = block.regions[0].bbox
    assert box.x0 < 0.2
    assert 0.4 < box.y0 < box.y1 < 0.9


def test_table_is_stored_as_markdown_and_cells(parsed: ParsedDocument) -> None:
    tables = [b for b in parsed.blocks if b.kind == BlockKind.TABLE]
    assert tables
    table = tables[0]
    assert table.text.lstrip().startswith("|")
    assert table.table is not None
    assert table.table.cells
    outer = table.regions[0].bbox
    for cell in table.table.cells:
        if cell.bbox:  # each cell lies inside the table, give or take rounding
            assert outer.x0 - 0.01 <= cell.bbox.x0 <= cell.bbox.x1 <= outer.x1 + 0.01
            assert outer.y0 - 0.01 <= cell.bbox.y0 <= cell.bbox.y1 <= outer.y1 + 0.01


def test_parser_records_its_versions(parsed: ParsedDocument) -> None:
    assert "docling" in parsed.parser
    assert "rapidocr" in parsed.parser


def test_the_real_parse_chunks_cleanly(parsed: ParsedDocument) -> None:
    config = ChunkingConfig()
    chunks = chunk_document(parsed, config)  # each ChunkDraft validates its own spans

    assert chunks
    assert all(c.token_count <= config.max_tokens for c in chunks if c.kind == "text")
    chunked = {i for c in chunks for i in c.block_indices}
    content = {
        b.index
        for b in parsed.blocks
        if b.kind not in (BlockKind.HEADING, BlockKind.TITLE, BlockKind.INDEX)
    }
    assert content <= chunked  # nothing citeable is lost
    paths = {title for c in chunks for title in c.section_path}
    for block in parsed.blocks:  # a heading may be a bare label, but one stating a fact
        if block.kind in (BlockKind.HEADING, BlockKind.TITLE) and ":" in block.text:
            title = block.section_path[-1]
            assert block.index in chunked or title in paths, f"heading lost: {title!r}"
