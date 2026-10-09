"""The section-aware chunker: boundaries, sizes, tables, and exact provenance."""

from app.ingest.chunking import ChunkDraft, ChunkingConfig, approx_tokens, chunk_document
from app.ingest.models import (
    BBox,
    Block,
    BlockKind,
    Page,
    ParsedDocument,
    Region,
    Table,
    TableCell,
)

CONFIG = ChunkingConfig(target_tokens=300, max_tokens=450, min_tokens=80)


def _box(i: int) -> BBox:
    top = (i % 20) * 0.045
    return BBox(x0=0.1, y0=top, x1=0.9, y1=top + 0.04)


def _block(
    index: int,
    kind: BlockKind,
    text: str,
    path: list[str],
    *,
    regions: list[Region] | None = None,
    table: Table | None = None,
) -> Block:
    return Block(
        index=index,
        kind=kind,
        text=text,
        regions=regions or [Region(page=1, bbox=_box(index), char_start=0, char_end=len(text))],
        section_path=path,
        table=table,
    )


def _doc(blocks: list[Block]) -> ParsedDocument:
    pages = [Page(number=n, width_pt=612, height_pt=792, has_text_layer=True) for n in (1, 2)]
    for i, block in enumerate(blocks):
        assert block.index == i
    return ParsedDocument(pages=pages, blocks=blocks, parser="test")


def _paragraph(words: int, seed: str = "word") -> str:
    """`words` words in sentences of ten."""
    sentences = [" ".join([seed] * 9) + " end." for _ in range(words // 10)]
    return " ".join(sentences)


def _source(doc: ParsedDocument, chunk: ChunkDraft) -> list[str]:
    return [doc.blocks[i].text for i in chunk.block_indices]


def test_chunks_never_cross_a_section_boundary() -> None:
    a, b = ["5 WARNINGS", "5.1 Thyroid"], ["5 WARNINGS", "5.2 Pancreatitis"]
    doc = _doc(
        [
            _block(0, BlockKind.HEADING, "5.1 Thyroid", a),
            _block(1, BlockKind.PARAGRAPH, "Tumors were seen in rats.", a),
            _block(2, BlockKind.HEADING, "5.2 Pancreatitis", b),
            _block(3, BlockKind.PARAGRAPH, "Discontinue if suspected.", b),
        ]
    )

    chunks = chunk_document(doc, CONFIG)

    assert [(c.section_path, c.block_indices) for c in chunks] == [(a, [0, 1]), (b, [2, 3])]
    assert chunks[0].text == "5.1 Thyroid\nTumors were seen in rats."


def test_a_heading_without_its_own_body_makes_no_chunk() -> None:
    top, sub = ["5 WARNINGS"], ["5 WARNINGS", "5.1 Thyroid"]
    doc = _doc(
        [
            _block(0, BlockKind.HEADING, "5 WARNINGS", top),
            _block(1, BlockKind.HEADING, "5.1 Thyroid", sub),
            _block(2, BlockKind.PARAGRAPH, "Tumors were seen in rats.", sub),
        ]
    )

    chunks = chunk_document(doc, CONFIG)

    assert [c.block_indices for c in chunks] == [[1, 2]]
    assert chunks[0].section_path == sub  # "5 WARNINGS" survives in the path


def test_blocks_are_packed_to_the_target_and_never_past_the_maximum() -> None:
    path = ["2 DOSAGE"]
    doc = _doc([_block(i, BlockKind.LIST_ITEM, _paragraph(40), path) for i in range(30)])

    chunks = chunk_document(doc, CONFIG)

    assert all(c.token_count <= CONFIG.max_tokens for c in chunks)
    assert all(c.token_count >= CONFIG.target_tokens * 0.6 for c in chunks[:-1])
    assert [i for c in chunks for i in c.block_indices] == list(range(30))  # all, once, in order


def test_a_small_chunk_may_grow_past_the_target_rather_than_end_tiny() -> None:
    path = ["1 INDICATIONS"]
    doc = _doc(
        [
            _block(0, BlockKind.PARAGRAPH, _paragraph(20), path),  # ~26 tokens
            _block(1, BlockKind.PARAGRAPH, _paragraph(250), path),  # ~325 tokens
        ]
    )

    chunks = chunk_document(doc, CONFIG)

    assert [c.block_indices for c in chunks] == [[0, 1]]


def test_a_long_paragraph_is_split_at_sentence_ends() -> None:
    text = _paragraph(1000)  # ~1300 tokens
    doc = _doc([_block(0, BlockKind.PARAGRAPH, text, ["Summary"])])

    chunks = chunk_document(doc, CONFIG)

    assert len(chunks) >= 3
    for chunk in chunks:
        assert chunk.token_count <= CONFIG.max_tokens
        assert chunk.text.endswith("end.")
        assert chunk.text in text  # an exact substring: nothing rewritten
    assert " ".join(c.text for c in chunks) == text


def test_every_character_maps_back_to_its_block_and_box() -> None:
    path = ["Results"]
    doc = _doc([_block(i, BlockKind.PARAGRAPH, f"Finding number {i}.", path) for i in range(5)])

    for chunk in chunk_document(doc, CONFIG):
        for span, block_index in zip(chunk.spans, chunk.block_indices, strict=True):
            quoted = chunk.text[span.char_start : span.char_end].rstrip("\n")
            block = doc.blocks[block_index]
            assert quoted == block.text
            assert (span.page, span.bbox) == (block.regions[0].page, block.regions[0].bbox)


def test_a_paragraph_across_two_pages_keeps_both_boxes() -> None:
    text = "Patients in the trial were followed for two years."
    split = text.index("followed")
    regions = [
        Region(page=1, bbox=_box(18), char_start=0, char_end=split),
        Region(page=2, bbox=_box(0), char_start=split, char_end=len(text)),
    ]
    doc = _doc([_block(0, BlockKind.PARAGRAPH, text, ["Results"], regions=regions)])

    (chunk,) = chunk_document(doc, CONFIG)

    assert [(s.page, s.char_start, s.char_end) for s in chunk.spans] == [
        (1, 0, split),
        (2, split, len(text)),
    ]


def _table(rows: int, words_per_row: int = 3) -> tuple[str, Table]:
    header = "| Trial | Result |"
    lines = [header, "|---|---|"]
    cells = [
        TableCell(row=0, col=c, row_span=1, col_span=1, text=t, is_header=True, bbox=_row_box(0))
        for c, t in enumerate(("Trial", "Result"))
    ]
    for r in range(1, rows + 1):
        result = " ".join(["better"] * words_per_row)
        lines.append(f"| Trial {r} | {result} |")
        cells += [
            TableCell(
                row=r,
                col=0,
                row_span=1,
                col_span=1,
                text=f"Trial {r}",
                is_header=False,
                bbox=_row_box(r),
            ),
            TableCell(
                row=r, col=1, row_span=1, col_span=1, text=result, is_header=False, bbox=_row_box(r)
            ),
        ]
    return "\n".join(lines), Table(n_rows=rows + 1, n_cols=2, cells=cells)


def _row_box(row: int) -> BBox:
    top = 0.2 + row * 0.012
    return BBox(x0=0.1, y0=top, x1=0.9, y1=top + 0.01)


def test_a_table_is_its_own_chunk_with_its_caption() -> None:
    path = ["14 CLINICAL STUDIES"]
    markdown, table = _table(rows=3)
    table_region = Region(
        page=1, bbox=BBox(x0=0.1, y0=0.2, x1=0.9, y1=0.3), char_start=0, char_end=len(markdown)
    )
    doc = _doc(
        [
            _block(0, BlockKind.PARAGRAPH, "Two trials were run.", path),
            _block(1, BlockKind.CAPTION, "Table 3: Results", path),
            _block(2, BlockKind.TABLE, markdown, path, regions=[table_region], table=table),
            _block(3, BlockKind.PARAGRAPH, "Both met their endpoints.", path),
        ]
    )

    chunks = chunk_document(doc, CONFIG)

    assert [c.kind for c in chunks] == ["text", "table", "text"]
    table_chunk = chunks[1]
    assert table_chunk.text == "Table 3: Results\n" + markdown
    assert (table_chunk.table_block, table_chunk.block_indices) == (2, [2, 1])
    caption_span, header_span, rows_span = table_chunk.spans
    assert caption_span.bbox == doc.blocks[1].regions[0].bbox
    assert header_span.bbox == _row_box(0)
    assert (rows_span.bbox.y0, rows_span.bbox.y1) == (_row_box(1).y0, _row_box(3).y1)


def test_a_big_table_is_split_by_rows_and_each_part_points_at_its_rows() -> None:
    markdown, table = _table(rows=40, words_per_row=12)  # ~800 tokens
    region = Region(
        page=1, bbox=BBox(x0=0.1, y0=0.2, x1=0.9, y1=0.8), char_start=0, char_end=len(markdown)
    )
    doc = _doc([_block(0, BlockKind.TABLE, markdown, ["Results"], regions=[region], table=table)])

    chunks = chunk_document(doc, CONFIG)

    assert len(chunks) >= 2
    header = "| Trial | Result |\n|---|---|"
    previous_bottom = 0.0
    for chunk in chunks:
        assert chunk.kind == "table"
        assert chunk.text.startswith(header + "\n")  # every part repeats the header
        assert chunk.token_count <= CONFIG.max_tokens
        rows_span = chunk.spans[-1]
        assert rows_span.bbox.y0 >= previous_bottom  # parts follow each other down the page
        previous_bottom = rows_span.bbox.y1
    data_rows = [line for c in chunks for line in c.text.split("\n")[2:]]
    assert data_rows == markdown.split("\n")[2:]  # every row once, in order


def test_tables_of_contents_are_skipped() -> None:
    doc = _doc(
        [
            _block(0, BlockKind.INDEX, "| 1 | Introduction | 3 |", []),
            _block(1, BlockKind.PARAGRAPH, "The review starts here.", []),
        ]
    )

    assert [c.block_indices for c in chunk_document(doc, CONFIG)] == [[1]]


def test_a_caption_far_from_any_table_is_ordinary_text() -> None:
    path = ["Figures"]
    doc = _doc(
        [
            _block(0, BlockKind.CAPTION, "Figure 2: Weight over time", path),
            _block(1, BlockKind.PARAGRAPH, "Weight fell steadily.", path),
        ]
    )

    (chunk,) = chunk_document(doc, CONFIG)

    assert (chunk.kind, chunk.block_indices) == ("text", [0, 1])


def test_chunking_is_deterministic() -> None:
    path = ["Results"]
    doc = _doc([_block(i, BlockKind.PARAGRAPH, _paragraph(50, f"w{i}"), path) for i in range(12)])

    assert chunk_document(doc, CONFIG) == chunk_document(doc, CONFIG)


def test_token_estimate_counts_words() -> None:
    assert approx_tokens("one two three four five six seven eight nine ten") == 13
