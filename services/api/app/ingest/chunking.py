"""ParsedDocument -> chunks, each knowing which page and box every character came from.

The rules, in order of importance:

1. Provenance: every character of a chunk's text maps to exactly one span (page,
   box, character range), end to end. Nothing is paraphrased; chunk text is made
   of exact substrings of block text, joined by single newlines.
2. Sections are hard boundaries: a chunk never mixes blocks from different
   sections (same section path), so "5.2 Acute Pancreatitis" stays one clean unit.
   A heading opens its section's first chunk; a heading with no body of its own
   ("5 WARNINGS AND PRECAUTIONS" followed directly by "5.1 ...") makes no chunk,
   but its text lives on in the section path of everything below it.
3. Size: blocks are packed up to `target_tokens`, never past `max_tokens` (bge-small
   reads 512 tokens; the rest is left for the section-path header). A chunk still
   under `min_tokens` may grow past the target rather than end tiny. A block too
   long on its own is split at sentence ends (or, failing that, between words).
4. Tables are their own chunks, with an adjacent caption. A table too big for one
   chunk is split into groups of rows, each repeating the header row; each part's
   box is the union of its rows' cell boxes, so a citation highlights the rows
   it actually quotes, not the whole table.
5. Tables of contents are skipped: they would match every query and answer none.
"""

import itertools
import re
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from typing import Literal, Self

from pydantic import BaseModel, Field, model_validator

from app.ingest.models import BBox, Block, BlockKind, ParsedDocument, Region, check_coverage

CHUNKER_VERSION = "sections-v1"
_TOKENS_PER_WORD = 1.3
_SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")
_MARKDOWN_RULE = re.compile(r"^\|?\s*:?-{3,}")


def approx_tokens(text: str) -> int:
    """Rough token count (words x 1.3) until the embedding model's tokenizer is chosen."""
    return round(len(text.split()) * _TOKENS_PER_WORD)


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
    # Ranges of `text` and where they sit: end to end, in order, no gaps.
    spans: list[Region] = Field(min_length=1)

    @model_validator(mode="after")
    def _spans_cover_text(self) -> Self:
        check_coverage(self.text, self.spans, whole=False)
        return self


@dataclass
class _Piece:
    """An exact substring of one block, with its spans rebased to the piece."""

    text: str
    spans: list[Region]
    block_index: int
    heading: bool

    @property
    def tokens(self) -> int:
        return approx_tokens(self.text)


def chunk_document(
    parsed: ParsedDocument, config: ChunkingConfig | None = None
) -> list[ChunkDraft]:
    config = config or ChunkingConfig()
    blocks = [b for b in parsed.blocks if b.kind != BlockKind.INDEX and b.text.strip()]
    captions = _captions_by_table(blocks)
    used_captions = set(captions.values())

    chunks: list[ChunkDraft] = []
    for path, section in itertools.groupby(blocks, key=lambda b: tuple(b.section_path)):
        pending: list[_Piece] = []
        for block in section:
            if block.index in used_captions:
                continue
            if block.kind == BlockKind.TABLE:
                chunks.extend(_flush(pending, list(path)))
                pending = []
                caption = next((b for b in blocks if b.index == captions.get(block.index)), None)
                chunks.extend(_table_chunks(block, caption, list(path), config))
                continue
            for piece in _pieces(block, config.max_tokens):
                if pending and not _fits(pending, piece, config):
                    chunks.extend(_flush(pending, list(path)))
                    pending = []
                pending.append(piece)
        chunks.extend(_flush(pending, list(path)))
    return chunks


def _captions_by_table(blocks: Sequence[Block]) -> dict[int, int]:
    """table block index -> caption block index, for a caption right before or after it."""
    found: dict[int, int] = {}
    taken: set[int] = set()
    for i, block in enumerate(blocks):
        if block.kind != BlockKind.TABLE:
            continue
        for j in (i - 1, i + 1):
            if 0 <= j < len(blocks):
                neighbour = blocks[j]
                same_section = neighbour.section_path == block.section_path
                if neighbour.kind == BlockKind.CAPTION and same_section and j not in taken:
                    found[block.index] = neighbour.index
                    taken.add(j)
                    break
    return found


def _fits(pending: list[_Piece], piece: _Piece, config: ChunkingConfig) -> bool:
    current = sum(p.tokens for p in pending)
    combined = current + piece.tokens
    if combined <= config.target_tokens:
        return True
    only_headings = all(p.heading for p in pending)
    small = current < config.min_tokens or only_headings
    return small and combined <= config.max_tokens


def _flush(pieces: list[_Piece], path: list[str]) -> list[ChunkDraft]:
    if not pieces or all(p.heading for p in pieces):
        return []  # nothing to chunk, or a heading with no body (it lives on in the path)
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
    """Pieces joined by newlines; each newline belongs to the span before it."""
    text_parts: list[str] = []
    spans: list[Region] = []
    offset = 0
    for i, piece in enumerate(pieces):
        last = i == len(pieces) - 1
        for j, span in enumerate(piece.spans):
            extend = 0 if last or j < len(piece.spans) - 1 else 1
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
    if approx_tokens(block.text) <= max_tokens:
        yield _Piece(block.text, list(block.regions), block.index, heading)
        return
    for start, end in _cut_points(block.text, max_tokens):
        yield _Piece(block.text[start:end], _clip(block.regions, start, end), block.index, heading)


def _cut_points(text: str, max_tokens: int) -> Iterator[tuple[int, int]]:
    words = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    max_words = max(int(max_tokens / _TOKENS_PER_WORD), 1)
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
    lines = table.text.split("\n")
    has_header = len(lines) >= 2 and bool(_MARKDOWN_RULE.match(lines[1].strip()))
    header, rows = (lines[:2], lines[2:]) if has_header else ([], lines)
    caption_tokens = approx_tokens(caption.text) if caption else 0
    budget = config.max_tokens - caption_tokens - approx_tokens("\n".join(header))

    groups: list[list[int]] = [[]]  # indices into `rows`
    used = 0
    for i, row in enumerate(rows):
        tokens = approx_tokens(row)
        if groups[-1] and used + tokens > budget:
            groups.append([])
            used = 0
        groups[-1].append(i)
        used += tokens

    row_boxes = _row_boxes(table, len(rows), has_header)
    page = table.regions[0].page
    chunks = []
    for group in groups:
        pieces: list[_Piece] = []
        if caption:
            pieces.append(_Piece(caption.text, list(caption.regions), caption.index, False))
        if header:
            box = row_boxes.get(0) if row_boxes else None
            pieces.append(_table_piece(table, "\n".join(header), page, box))
        group_text = "\n".join(rows[i] for i in group)
        box = _union(row_boxes[i + 1] for i in group if i + 1 in row_boxes) if row_boxes else None
        pieces.append(_table_piece(table, group_text, page, box))
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


def _table_piece(table: Block, text: str, page: int, box: BBox | None) -> _Piece:
    """Table text located by its rows' box, or by the table's own regions if unknown."""
    if box is not None:
        spans = [Region(page=page, bbox=box, char_start=0, char_end=len(text))]
    else:
        first, *rest = table.regions
        spans = [first.model_copy(update={"char_start": 0, "char_end": len(text)})]
        # A table across pages: the rows can't be placed, so later pages get empty spans.
        spans += [
            r.model_copy(update={"char_start": len(text), "char_end": len(text)}) for r in rest
        ]
    return _Piece(text, spans, table.index, False)


def _row_boxes(table: Block, markdown_rows: int, has_header: bool) -> dict[int, BBox]:
    """Table row index -> box around its cells, when the Markdown rows line up with the cells.

    Docling writes row 0 as the Markdown header, then one line per further row. If the
    counts don't match (merged header rows), rows aren't placed and whole-table boxes
    are used instead.
    """
    if table.table is None or len(table.regions) != 1:
        return {}
    expected = table.table.n_rows - (1 if has_header else 0)
    if markdown_rows != expected:
        return {}
    boxes: dict[int, list[BBox]] = {}
    for cell in table.table.cells:
        if cell.bbox is None:
            continue
        for row in range(cell.row, cell.row + cell.row_span):
            boxes.setdefault(row, []).append(cell.bbox)
    return {row: _bounding(cells) for row, cells in boxes.items()}


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
