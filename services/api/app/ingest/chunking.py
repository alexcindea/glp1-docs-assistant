"""ParsedDocument -> chunks, each knowing which page and box every character came from.

The rules, in order of importance:

1. Provenance: every character of a chunk's text maps to a span (page, box,
   character range), end to end. Text chunks are exact substrings of block text
   joined by single newlines, so each span is a precise slice of a block. Table
   chunks are rebuilt from the table's cells (see 4), so their spans point at the
   rows' boxes rather than into the block's Markdown. When a table's rows can't be
   placed on its pages, the same text gets one span per page: "it is in one of these".
2. Nothing citeable is dropped. Sections are hard boundaries: a chunk never mixes
   blocks from different sections, so "5.2 Acute Pancreatitis" stays one clean unit.
   A heading opens its section's first chunk. A heading with no body whose
   subsections follow ("5 WARNINGS" then "5.1 ...") makes no chunk of its own: it
   lives on in their section paths. One with neither becomes a small chunk if it
   states a fact (Docling labels lines like "Initial U.S. Approval: 2005" as
   headings); a bare label with nothing under it is dropped.
3. Size: blocks are packed up to `target_tokens`, never past `max_tokens` (bge-small
   reads 512 tokens; the rest is left for the section-path header). A chunk still
   under `min_tokens` may grow past the target rather than end tiny. A block too
   long on its own is split at sentence ends (or, failing that, between words).
   Sizes are budgeted in words, which add up exactly when texts are joined.
4. Tables are their own chunks, with an adjacent caption. Rows are rebuilt from the
   cells: a cell spanning columns is written once (Docling's Markdown repeats it in
   every column), a cell spanning rows is repeated in each row (so a part of a split
   table still says which arm or study a row belongs to). A table too big for one
   chunk is split into groups of rows, each repeating the header; each part's box is
   the union of its rows' boxes, so a citation highlights the rows it quotes.
5. Tables of contents are skipped: they would match every query and answer none.
"""

import itertools
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.ingest.models import BBox, Block, BlockKind, ParsedDocument, Region, check_coverage

CHUNKER_VERSION = "sections-v3"
_TOKENS_PER_WORD = 1.3
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")
_MARKDOWN_RULE = re.compile(r"^\|?\s*:?-{3,}")
# "Label: value": a heading that carries a fact of its own.
_FACT_HEADING = re.compile(r"\w\s*:\s*\w")

Line = tuple[int | None, str]  # (table row it shows, or None if unknown; Markdown text)


def approx_tokens(text: str) -> int:
    """Rough token count (words x 1.3) until the embedding model's tokenizer is chosen."""
    return round(_words(text) * _TOKENS_PER_WORD)


def _words(text: str) -> int:
    return len(text.split())


def _word_limit(tokens: int) -> int:
    return max(int(tokens / _TOKENS_PER_WORD), 1)


@dataclass(frozen=True)
class ChunkingConfig:
    target_tokens: int = 300
    max_tokens: int = 450
    min_tokens: int = 80


class ChunkDraft(BaseModel):
    kind: Literal["text", "table"]
    text: str = Field(min_length=1)
    section_path: list[str]
    token_count: int = Field(ge=0)
    block_indices: list[int] = Field(min_length=1)
    table_block: int | None = None  # the table's block index, for table chunks
    # Ranges of `text` and where they sit: end to end, in order, no gaps. A table
    # chunk may give one range several spans, one per page it may be on.
    spans: list[Region] = Field(min_length=1)

    @model_validator(mode="after")
    def _spans_cover_text(self) -> Self:
        if self.kind == "table":
            _check_alternatives(self.text, self.spans)
        else:
            check_coverage(self.text, self.spans, whole=False)
        return self


def _check_alternatives(text: str, spans: Sequence[Region]) -> None:
    """Like check_coverage, but identical ranges may repeat (one span per page)."""
    ranges = list(dict.fromkeys((s.char_start, s.char_end) for s in spans))
    expected = 0
    for start, end in ranges:
        if start != expected or end < start:
            raise ValueError(f"spans must cover the text end to end, got {ranges}")
        expected = end
    if expected != len(text):
        raise ValueError(f"spans end at {expected}, text has {len(text)} characters")


@dataclass
class _Piece:
    """Text with its spans rebased to the piece (an exact substring, for text blocks)."""

    text: str
    spans: list[Region]
    block_index: int
    heading: bool

    @property
    def words(self) -> int:
        return _words(self.text)


def chunk_document(
    parsed: ParsedDocument, config: ChunkingConfig | None = None
) -> list[ChunkDraft]:
    config = config or ChunkingConfig()
    blocks = [b for b in parsed.blocks if b.kind != BlockKind.INDEX and b.text.strip()]
    captions = _captions_by_table(blocks, config)
    used_captions = set(captions.values())
    by_index = {b.index: b for b in blocks}
    sections = [
        (list(path), list(group))
        for path, group in itertools.groupby(blocks, key=lambda b: tuple(b.section_path))
    ]

    chunks: list[ChunkDraft] = []
    for position, (path, section) in enumerate(sections):
        emitted = len(chunks)
        pending: list[_Piece] = []
        for block in section:
            if block.index in used_captions:
                continue
            if block.kind == BlockKind.TABLE:
                chunks.extend(_flush(pending, path))
                pending = []
                caption = by_index.get(captions.get(block.index, -1))
                table_chunks = _table_chunks(block, caption, path, config)
                chunks.extend(table_chunks)
                if not table_chunks and caption:  # an empty table: keep its caption
                    pending.append(
                        _Piece(caption.text, list(caption.regions), caption.index, False)
                    )
                continue
            for piece in _pieces(block, config.max_tokens):
                if pending and not _fits(pending, piece, config):
                    chunks.extend(_flush(pending, path))
                    pending = []
                pending.append(piece)
        # A heading alone is kept only if nothing else carries it: no chunk in its own
        # section and no subsection after it (whose paths would include it).
        nothing_else = len(chunks) == emitted and not _has_subsection(sections, position)
        chunks.extend(_flush(pending, path, keep_headings=nothing_else))
    return chunks


def _has_subsection(sections: list[tuple[list[str], list[Block]]], position: int) -> bool:
    path = sections[position][0]
    if position + 1 >= len(sections):
        return False
    following = sections[position + 1][0]
    return len(following) > len(path) and following[: len(path)] == path


def _captions_by_table(blocks: Sequence[Block], config: ChunkingConfig) -> dict[int, int]:
    """table block index -> caption block index, for a short caption right before or after it."""
    found: dict[int, int] = {}
    taken: set[int] = set()
    longest = _word_limit(config.max_tokens) // 3  # a longer "caption" is text in its own right
    for i, block in enumerate(blocks):
        if block.kind != BlockKind.TABLE:
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(blocks):
                neighbour = blocks[j]
                if (
                    neighbour.kind == BlockKind.CAPTION
                    and neighbour.section_path == block.section_path
                    and _words(neighbour.text) <= longest
                    and j not in taken
                ):
                    found[block.index] = neighbour.index
                    taken.add(j)
                    break
    return found


def _fits(pending: list[_Piece], piece: _Piece, config: ChunkingConfig) -> bool:
    current = sum(p.words for p in pending)
    combined = current + piece.words
    if combined <= _word_limit(config.target_tokens):
        return True
    only_headings = all(p.heading for p in pending)
    small = current < _word_limit(config.min_tokens) or only_headings
    return small and combined <= _word_limit(config.max_tokens)


def _flush(
    pieces: list[_Piece], path: list[str], *, keep_headings: bool = False
) -> list[ChunkDraft]:
    if pieces and all(p.heading for p in pieces):
        # Headings alone: they live on in section paths. One that nothing else carries
        # is kept only if it states a fact ("Initial U.S. Approval: 2005"); bare labels
        # ("Pharmacokinetics", repeated page banners) would only add noise to search.
        pieces = [p for p in pieces if keep_headings and _FACT_HEADING.search(p.text)]
    if not pieces:
        return []
    text, spans = _join(pieces)
    return [
        ChunkDraft(
            kind="text",
            text=text,
            section_path=path,
            token_count=approx_tokens(text),
            block_indices=list(dict.fromkeys(p.block_index for p in pieces)),
            spans=spans,
        )
    ]


def _join(pieces: Sequence[_Piece]) -> tuple[str, list[Region]]:
    """Pieces joined by newlines; each newline belongs to the span(s) ending before it."""
    text_parts: list[str] = []
    spans: list[Region] = []
    offset = 0
    for i, piece in enumerate(pieces):
        last = i == len(pieces) - 1
        piece_end = max((s.char_end for s in piece.spans), default=0)
        for span in piece.spans:
            extend = 0 if last or span.char_end != piece_end else 1
            spans.append(
                span.model_copy(
                    update={
                        "char_start": span.char_start + offset,
                        "char_end": span.char_end + offset + extend,
                    }
                )
            )
        text_parts.append(piece.text)
        offset += len(piece.text) + (0 if last else 1)
    return "\n".join(text_parts), spans


def _pieces(block: Block, max_tokens: int) -> Iterator[_Piece]:
    """The block whole if it fits, otherwise cut at sentence ends (or between words)."""
    heading = block.kind in (BlockKind.HEADING, BlockKind.TITLE)
    max_words = _word_limit(max_tokens)
    if _words(block.text) <= max_words:
        yield _Piece(block.text, list(block.regions), block.index, heading)
        return
    for start, end in _cut_points(block.text, max_words):
        yield _Piece(block.text[start:end], _clip(block.regions, start, end), block.index, heading)


def _cut_points(text: str, max_words: int) -> Iterator[tuple[int, int]]:
    words = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    i = 0
    while i < len(words):
        window = words[i : i + max_words]
        cut = len(window)
        if i + len(window) < len(words):  # not the last piece: prefer a sentence end
            ends = [k + 1 for k, (s, e) in enumerate(window) if _SENTENCE_END.search(text[s:e])]
            if ends and ends[-1] >= len(window) // 2:
                cut = ends[-1]
        yield words[i][0], words[i + cut - 1][1]
        i += cut


def _clip(regions: Sequence[Region], start: int, end: int) -> list[Region]:
    """The parts of a block's regions inside [start, end), rebased to start at 0."""
    clipped = []
    for region in regions:
        s, e = max(region.char_start, start), min(region.char_end, end)
        if s < e:
            clipped.append(
                region.model_copy(update={"char_start": s - start, "char_end": e - start})
            )
    return clipped


def _table_chunks(
    table: Block, caption: Block | None, path: list[str], config: ChunkingConfig
) -> list[ChunkDraft]:
    header, rows = _table_lines(table)
    if header and not rows:  # only a header row: that is the content
        rows = [line for line in header if not _MARKDOWN_RULE.match(line[1])]
        header = []
    if not rows:
        return []  # a table with no text in it: nothing to search or cite
    max_words = _word_limit(config.max_tokens)
    if header and sum(_words(text) for _, text in header) > max_words // 3:
        # A "header" this big is content (EMA frames whole study summaries as tables),
        # not column titles: don't repeat it on every part.
        rows = [line for line in header if not _MARKDOWN_RULE.match(line[1])] + rows
        header = []
    header_text = "\n".join(text for _, text in header)
    caption_words = _words(caption.text) if caption else 0
    budget = max(max_words - caption_words - _words(header_text), 1)  # words for rows

    # A row too big for one chunk is cut between words; each piece keeps its row.
    units: list[Line] = []
    for row, line in rows:
        if _words(line) <= budget:
            units.append((row, line))
        else:
            units.extend((row, line[start:end]) for start, end in _cut_points(line, budget))

    groups: list[list[Line]] = [[]]
    used = 0
    for unit in units:
        words = _words(unit[1])
        if groups[-1] and used + words > budget:
            groups.append([])
            used = 0
        groups[-1].append(unit)
        used += words

    row_boxes = _row_boxes(table)
    header_box = _union(row_boxes[r] for r, _ in header if r is not None and r in row_boxes)
    chunks = []
    for group in groups:
        pieces: list[_Piece] = []
        if caption:
            pieces.append(_Piece(caption.text, list(caption.regions), caption.index, False))
        if header:
            pieces.append(_table_piece(table, header_text, header_box))
        group_rows = dict.fromkeys(r for r, _ in group if r is not None)
        box = _union(row_boxes[r] for r in group_rows if r in row_boxes)
        pieces.append(_table_piece(table, "\n".join(text for _, text in group), box))
        text, spans = _join([p for p in pieces if p.text])
        chunks.append(
            ChunkDraft(
                kind="table",
                text=text,
                section_path=path,
                token_count=approx_tokens(text),
                block_indices=[table.index] + ([caption.index] if caption else []),
                table_block=table.index,
                spans=spans,
            )
        )
    return chunks


def _table_lines(table: Block) -> tuple[list[Line], list[Line]]:
    """(header lines, row lines) as Markdown, each with the table row it shows.

    Built from the cells when they have text; otherwise from Docling's Markdown
    (OCR can find a table's frame but no words in it), whose rows can't be placed.
    """
    data = table.table
    rows: list[Line] = []
    if data is not None and data.n_rows and data.n_cols:
        grid = [["" for _ in range(data.n_cols)] for _ in range(data.n_rows)]
        for cell in data.cells:
            text = " ".join(cell.text.split()).replace("|", "\\|")
            if not 0 <= cell.col < data.n_cols:
                continue
            for row in range(cell.row, min(cell.row + cell.row_span, data.n_rows)):
                grid[row][cell.col] = text  # repeated down the rows, written once across
        rows = [
            (r, "| " + " | ".join(grid[r]) + " |")
            for r in range(data.n_rows)
            if any(_has_words(text) for text in grid[r])
        ]
    if rows and data is not None:
        rule: Line = (None, "|" + "---|" * data.n_cols)
        return [rows[0], rule], rows[1:]

    markdown = table.text.split("\n")
    if len(markdown) >= 2 and _MARKDOWN_RULE.match(markdown[1].strip()):
        header: list[Line] = [(None, markdown[0]), (None, markdown[1])]
        body = markdown[2:]
    else:
        header, body = [], markdown
    if header and not _has_words(header[0][1]):
        header = []
    return header, [(None, line) for line in body if _has_words(line)]


def _has_words(text: str) -> bool:
    return any(ch.isalnum() for ch in text)


def _table_piece(table: Block, text: str, box: BBox | None) -> _Piece:
    """Table text located by its rows' box; failing that, by every page's table region."""
    if box is not None:
        page = table.regions[0].page
        spans = [Region(page=page, bbox=box, char_start=0, char_end=len(text))]
    else:
        spans = [
            r.model_copy(update={"char_start": 0, "char_end": len(text)}) for r in table.regions
        ]
    return _Piece(text, spans, table.index, False)


def _row_boxes(table: Block) -> dict[int, BBox]:
    """Table row index -> box around the row (single-page tables only).

    Built from cells that sit in that row alone where there are any, so a row's box
    isn't stretched by a cell merged down several rows; rows with only merged cells
    get the merged cells' box.
    """
    if table.table is None or len(table.regions) != 1:
        return {}
    own: dict[int, list[BBox]] = {}
    shared: dict[int, list[BBox]] = {}
    for cell in table.table.cells:
        if cell.bbox is None:
            continue
        target = own if cell.row_span == 1 else shared
        for row in range(cell.row, cell.row + cell.row_span):
            target.setdefault(row, []).append(cell.bbox)
    rows = own.keys() | shared.keys()
    return {row: _bounding(own.get(row) or shared[row]) for row in rows}


def _union(boxes: Iterator[BBox]) -> BBox | None:
    items = list(boxes)
    return _bounding(items) if items else None


def _bounding(items: Sequence[BBox]) -> BBox:
    return BBox(
        x0=min(b.x0 for b in items),
        y0=min(b.y0 for b in items),
        x1=max(b.x1 for b in items),
        y1=max(b.y1 for b in items),
    )
