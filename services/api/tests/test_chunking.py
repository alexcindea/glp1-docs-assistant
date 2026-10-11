"""The section-aware chunker: boundaries, sizes, tables, and exact provenance."""

import pytest
from pydantic import ValidationError

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


def test_a_framed_text_box_read_as_a_table_still_fits() -> None:
    # EMA study summaries: a frame Docling reads as a table whose "header" is a wall of text.
    wall = " ".join(["The trial enrolled adults with type 2 diabetes."] * 150)  # ~1300 tokens
    markdown = f"| | {wall} |\n|---|---|\n| | Results follow. |"
    region = Region(
        page=1, bbox=BBox(x0=0.1, y0=0.1, x1=0.9, y1=0.9), char_start=0, char_end=len(markdown)
    )
    table = Table(n_rows=2, n_cols=2, cells=[])
    doc = _doc(
        [_block(0, BlockKind.TABLE, markdown, ["Clinical efficacy"], regions=[region], table=table)]
    )

    chunks = chunk_document(doc, CONFIG)

    assert len(chunks) >= 3
    for chunk in chunks:
        assert chunk.token_count <= CONFIG.max_tokens
        for line in chunk.text.split("\n"):
            assert line in markdown  # exact pieces of the original, nothing rewritten
        assert chunk.spans[0].bbox == region.bbox  # rows can't be placed: the table's box
    assert "".join(c.text.replace("\n", " ") for c in chunks).count("type 2 diabetes") == 150


def test_a_merged_cell_is_written_once() -> None:
    # Docling's Markdown repeats a cell spanning three columns three times.
    cells = [
        TableCell(
            row=0, col=0, row_span=1, col_span=1, text="Trial", is_header=True, bbox=_row_box(0)
        ),
        TableCell(
            row=0, col=1, row_span=1, col_span=3, text="Outcome", is_header=True, bbox=_row_box(0)
        ),
        TableCell(
            row=1, col=0, row_span=1, col_span=1, text="STEP 1", is_header=False, bbox=_row_box(1)
        ),
        TableCell(
            row=1,
            col=1,
            row_span=1,
            col_span=3,
            text="-14.9% vs -2.4%",
            is_header=False,
            bbox=_row_box(1),
        ),
    ]
    docling_markdown = (
        "| Trial | Outcome | Outcome | Outcome |\n|---|---|---|---|\n"
        "| STEP 1 | -14.9% vs -2.4% | -14.9% vs -2.4% | -14.9% vs -2.4% |"
    )
    region = Region(
        page=1,
        bbox=BBox(x0=0.1, y0=0.2, x1=0.9, y1=0.3),
        char_start=0,
        char_end=len(docling_markdown),
    )
    table = Table(n_rows=2, n_cols=4, cells=cells)
    doc = _doc(
        [_block(0, BlockKind.TABLE, docling_markdown, ["Results"], regions=[region], table=table)]
    )

    (chunk,) = chunk_document(doc, CONFIG)

    assert (
        chunk.text
        == "| Trial | Outcome |  |  |\n|---|---|---|---|\n| STEP 1 | -14.9% vs -2.4% |  |  |"
    )
    assert chunk.text.count("-14.9%") == 1
    assert [s.bbox for s in chunk.spans] == [_row_box(0), _row_box(1)]


def test_a_table_without_any_text_makes_no_chunk() -> None:
    # A scanned page: OCR found the table's frame but no words in its cells.
    cells = [
        TableCell(row=r, col=c, row_span=1, col_span=1, text="", is_header=r == 0, bbox=_row_box(r))
        for r in range(2)
        for c in range(2)
    ]
    markdown = "|  |  |\n|---|---|\n|  |  |"
    region = Region(
        page=1, bbox=BBox(x0=0.1, y0=0.2, x1=0.9, y1=0.3), char_start=0, char_end=len(markdown)
    )
    path = ["Results"]
    doc = _doc(
        [
            _block(
                0,
                BlockKind.TABLE,
                markdown,
                path,
                regions=[region],
                table=Table(n_rows=2, n_cols=2, cells=cells),
            ),
            _block(1, BlockKind.PARAGRAPH, "The trial met its endpoint.", path),
        ]
    )

    assert [c.block_indices for c in chunk_document(doc, CONFIG)] == [[1]]


def test_a_header_only_table_is_kept_as_content() -> None:
    cells = [
        TableCell(row=0, col=c, row_span=1, col_span=1, text=t, is_header=True, bbox=_row_box(0))
        for c, t in enumerate(("Dose", "Frequency"))
    ]
    markdown = "| Dose | Frequency |\n|---|---|"
    region = Region(
        page=1, bbox=BBox(x0=0.1, y0=0.2, x1=0.9, y1=0.3), char_start=0, char_end=len(markdown)
    )
    doc = _doc(
        [
            _block(
                0,
                BlockKind.TABLE,
                markdown,
                ["Dosing"],
                regions=[region],
                table=Table(n_rows=1, n_cols=2, cells=cells),
            )
        ]
    )

    (chunk,) = chunk_document(doc, CONFIG)

    assert chunk.text == "| Dose | Frequency |"


def _cell(
    row: int, col: int, text: str, *, rows: int = 1, cols: int = 1, box: BBox | None = None
) -> TableCell:
    return TableCell(
        row=row,
        col=col,
        row_span=rows,
        col_span=cols,
        text=text,
        is_header=row == 0,
        bbox=box or _row_box(row),
    )


def _table_block(
    index: int,
    cells: list[TableCell],
    n_rows: int,
    n_cols: int,
    regions: list[Region],
    path: list[str],
    text: str = "| (Docling Markdown, unused when cells have text) |",
) -> Block:
    return Block(
        index=index,
        kind=BlockKind.TABLE,
        text=text,
        regions=[r.model_copy(update={"char_start": 0, "char_end": len(text)}) for r in regions],
        section_path=path,
        table=Table(n_rows=n_rows, n_cols=n_cols, cells=cells),
    )


ONE_PAGE = [Region(page=1, bbox=BBox(x0=0.1, y0=0.2, x1=0.9, y1=0.8), char_start=0, char_end=1)]


def test_a_table_across_pages_keeps_every_page() -> None:
    regions = [
        Region(page=1, bbox=BBox(x0=0.1, y0=0.7, x1=0.9, y1=0.95), char_start=0, char_end=1),
        Region(page=2, bbox=BBox(x0=0.1, y0=0.05, x1=0.9, y1=0.3), char_start=0, char_end=1),
    ]
    cells = [_cell(0, 0, "Arm"), _cell(1, 0, "Placebo"), _cell(2, 0, "Drug")]
    doc = _doc([_table_block(0, cells, 3, 1, regions, ["Results"])])

    (chunk,) = chunk_document(doc, CONFIG)

    rows_spans = [s for s in chunk.spans if s.char_end == len(chunk.text)]
    assert {s.page for s in rows_spans} == {1, 2}  # it may be on either page, not just page 1


def test_a_heading_with_no_body_and_no_subsections_is_kept() -> None:
    doc = _doc(
        [
            _block(
                0, BlockKind.HEADING, "Initial U.S. Approval: 2005", ["Initial U.S. Approval: 2005"]
            ),
            _block(1, BlockKind.HEADING, "1 INDICATIONS", ["1 INDICATIONS"]),
            _block(2, BlockKind.PARAGRAPH, "For type 2 diabetes.", ["1 INDICATIONS"]),
        ]
    )

    chunks = chunk_document(doc, CONFIG)

    assert [c.text for c in chunks] == [
        "Initial U.S. Approval: 2005",
        "1 INDICATIONS\nFor type 2 diabetes.",
    ]


def test_the_caption_of_an_empty_table_is_kept_as_text() -> None:
    path = ["Results"]
    empty = [_cell(r, c, "") for r in range(2) for c in range(2)]
    doc = _doc(
        [
            _block(0, BlockKind.CAPTION, "Table 2: Adverse events by arm", path),
            _table_block(1, empty, 2, 2, ONE_PAGE, path, text="|  |  |"),
        ]
    )

    (chunk,) = chunk_document(doc, CONFIG)

    assert (chunk.kind, chunk.text) == ("text", "Table 2: Adverse events by arm")


def test_a_cell_merged_down_rows_labels_each_row_and_boxes_stay_tight() -> None:
    label_box = BBox(x0=0.1, y0=_row_box(1).y0, x1=0.3, y1=_row_box(2).y1)
    cells = [
        _cell(0, 0, "Arm"),
        _cell(0, 1, "Dose"),
        _cell(1, 0, "Placebo", rows=2, box=label_box),
        _cell(1, 1, "0 mg"),
        _cell(2, 1, "0 mg weekly"),
    ]
    doc = _doc([_table_block(0, cells, 3, 2, ONE_PAGE, ["Results"])])

    (chunk,) = chunk_document(doc, CONFIG)

    assert chunk.text.split("\n")[2:] == ["| Placebo | 0 mg |", "| Placebo | 0 mg weekly |"]
    rows_span = chunk.spans[-1]
    assert (rows_span.bbox.y0, rows_span.bbox.y1) == (_row_box(1).y0, _row_box(2).y1)


def test_each_part_of_a_split_table_boxes_exactly_its_own_rows() -> None:
    cells = [_cell(0, 0, "Trial"), _cell(0, 1, "Result")]
    for r in range(1, 41):
        cells += [_cell(r, 0, f"Trial {r}"), _cell(r, 1, " ".join(["better"] * 12))]
    doc = _doc([_table_block(0, cells, 41, 2, ONE_PAGE, ["Results"])])

    chunks = chunk_document(doc, CONFIG)

    assert len(chunks) >= 2
    for chunk in chunks:
        numbers = [int(line.split("|")[1].split()[1]) for line in chunk.text.split("\n")[2:]]
        rows_span = chunk.spans[-1]
        assert rows_span.bbox.y0 == _row_box(numbers[0]).y0
        assert rows_span.bbox.y1 == _row_box(numbers[-1]).y1


def test_a_row_cut_between_words_keeps_its_rows_box() -> None:
    wall = " ".join(["The trial enrolled adults with type 2 diabetes."] * 120)
    cells = [
        _cell(0, 0, "Trial"),
        _cell(0, 1, "Design"),
        _cell(1, 0, "SUSTAIN 1"),
        _cell(1, 1, wall),
    ]
    doc = _doc([_table_block(0, cells, 2, 2, ONE_PAGE, ["Clinical efficacy"])])

    chunks = chunk_document(doc, CONFIG)

    assert len(chunks) >= 2
    assert all(c.token_count <= CONFIG.max_tokens for c in chunks)
    assert all(c.spans[-1].bbox == _row_box(1) for c in chunks)


def test_a_caption_after_its_table_is_attached_but_a_long_one_is_not() -> None:
    path = ["Results"]
    cells = [_cell(0, 0, "Arm"), _cell(1, 0, "Placebo")]
    doc = _doc(
        [
            _table_block(0, cells, 2, 1, ONE_PAGE, path),
            _block(1, BlockKind.CAPTION, "Table 1: Arms", path),
        ]
    )
    (chunk,) = chunk_document(doc, CONFIG)
    assert chunk.text.startswith("Table 1: Arms\n")

    long_caption = " ".join(["word"] * 200)
    doc = _doc(
        [
            _block(0, BlockKind.CAPTION, long_caption, path),
            _table_block(1, cells, 2, 1, ONE_PAGE, path),
        ]
    )
    kinds = [(c.kind, c.block_indices) for c in chunk_document(doc, CONFIG)]
    assert kinds == [("text", [0]), ("table", [1])]
    assert all(c.token_count <= CONFIG.max_tokens for c in chunk_document(doc, CONFIG))


def test_a_lone_heading_without_a_fact_is_dropped() -> None:
    doc = _doc(
        [
            _block(0, BlockKind.HEADING, "CENTER FOR DRUG EVALUATION AND RESEARCH", ["CENTER"]),
            _block(1, BlockKind.HEADING, "1 INDICATIONS", ["1 INDICATIONS"]),
            _block(2, BlockKind.PARAGRAPH, "For type 2 diabetes.", ["1 INDICATIONS"]),
        ]
    )

    assert [c.block_indices for c in chunk_document(doc, CONFIG)] == [[1, 2]]


def _two_page_table(second_first_row: str, *, between: bool = False) -> ParsedDocument:
    """A 2-column table on page 1 (caption, header, one row) and one on page 2."""
    path = ["6 ADVERSE REACTIONS"]
    page_1 = [Region(page=1, bbox=BBox(x0=0.1, y0=0.6, x1=0.9, y1=0.95), char_start=0, char_end=1)]
    page_2 = [Region(page=2, bbox=BBox(x0=0.1, y0=0.05, x1=0.9, y1=0.3), char_start=0, char_end=1)]
    first = [
        _cell(0, 0, "Reaction"),
        _cell(0, 1, "15 mg %"),
        _cell(1, 0, "Nausea"),
        _cell(1, 1, "18"),
    ]
    second = [
        _cell(0, 0, second_first_row),
        _cell(0, 1, "17" if second_first_row == "Diarrhea" else "15 mg %"),
        _cell(1, 0, "Vomiting"),
        _cell(1, 1, "9"),
    ]
    blocks = [
        _block(0, BlockKind.CAPTION, "Table 1: Adverse Reactions", path),
        _table_block(1, first, 2, 2, page_1, path),
    ]
    if between:
        blocks.append(_block(2, BlockKind.PARAGRAPH, "Note: percentages of patients.", path))
    blocks.append(_table_block(len(blocks), second, 2, 2, page_2, path))
    return _doc(blocks)


def test_a_table_continued_on_the_next_page_gets_the_caption_and_header_where_they_are() -> None:
    doc = _two_page_table("Diarrhea")

    first, second = chunk_document(doc, CONFIG)

    assert second.text.splitlines() == [
        "Table 1: Adverse Reactions",
        "| Reaction | 15 mg % |",
        "|---|---|",
        "| Diarrhea | 17 |",
        "| Vomiting | 9 |",
    ]
    caption_span, header_span, rows_span = second.spans
    assert (caption_span.page, header_span.page, rows_span.page) == (1, 1, 2)
    assert header_span.bbox == _row_box(0)  # the header row of the first part
    assert second.text[rows_span.char_start : rows_span.char_end].startswith("| Diarrhea")
    assert sorted(second.block_indices) == [0, 1, 2]
    assert first.text.startswith("Table 1: Adverse Reactions\n| Reaction")


def test_a_next_page_table_that_repeats_its_header_is_left_alone() -> None:
    _, second = chunk_document(_two_page_table("Reaction"), CONFIG)

    assert second.text.splitlines()[0] == "| Reaction | 15 mg % |"
    assert {s.page for s in second.spans} == {2}


def test_a_table_after_text_is_not_a_continuation() -> None:
    chunks = chunk_document(_two_page_table("Diarrhea", between=True), CONFIG)

    assert chunks[-1].text.splitlines()[0] == "| Diarrhea | 17 |"


@pytest.mark.parametrize(
    ("ranges", "says"),
    [
        ([(0, 4), (6, 11)], "end to end"),  # a gap: characters 4-5 map to no page
        ([(0, 4), (0, 4)], "spans end at 4"),  # a repeated range, but the text runs on
    ],
)
def test_a_table_chunk_whose_spans_miss_text_is_refused(
    ranges: list[tuple[int, int]], says: str
) -> None:
    with pytest.raises(ValidationError, match=says):
        ChunkDraft(
            kind="table",
            text="| a | b |\n| 1 |",
            section_path=["Table 1"],
            token_count=4,
            block_indices=[0],
            table_block=0,
            spans=[
                Region(page=1 + i, bbox=_box(i), char_start=start, char_end=end)
                for i, (start, end) in enumerate(ranges)
            ],
        )
