"""Document structure the parser can't give us directly: section depth and boxes.

Docling labels every heading as level 1, so the hierarchy comes from heading
numbers instead: "2.4.1 Introduction" is depth 3. An unnumbered heading
("Identified Risks") sits one level below the last numbered one, unless it is in
capitals ("FULL PRESCRIBING INFORMATION"): regulatory documents use capitals for
top-level headings, and without this rule they would nest under whatever
numbered heading came before (often a table-of-contents line). Documents without
numbering stay flat, which is still correct, just shallower.

Docling sometimes labels a table's caption ("Table 1: Adverse Reactions ...") as
a heading. Left alone it would open a section, file the notes under the table in
it, and exist only as a section title rather than as citeable text. A heading
that reads like a table caption and comes right before a table is treated as
that table's caption.
"""

import re
from collections.abc import Sequence
from dataclasses import dataclass

from app.ingest.models import BBox, Block, BlockKind, ParsedDocument

# Leading section number: "2", "2.4", "2.4.1.", "Section 3". The number must start
# the heading and be followed by whitespace or the end.
_SECTION_NUMBER = re.compile(r"^(?:section\s+)?(\d{1,2}(?:\.\d{1,2})*)\.?(?=\s|$)", re.IGNORECASE)
# Runs of decoration FDA labels put around headings: "------ DOSAGE ------".
_DECORATION = re.compile(r"[-_=~*•·]{2,}")
# "Table 1:", "Table 2B.", "TABLE 14 -": the opening of a table caption.
_TABLE_CAPTION = re.compile(
    r"^table\s+[a-z]?\d+[a-z]?\s*[:.\-\N{EN DASH}\N{EM DASH}]", re.IGNORECASE
)


def clean_heading(text: str) -> str:
    """Heading text without decoration or surrounding whitespace."""
    return re.sub(r"\s+", " ", _DECORATION.sub(" ", text)).strip()


def numbered_depth(heading: str) -> int | None:
    """Depth from a leading section number, or None if the heading isn't numbered."""
    match = _SECTION_NUMBER.match(heading)
    return len(match.group(1).split(".")) if match else None


def is_capitalised(heading: str) -> bool:
    """All letters upper case, in at least two words: a heading, not an acronym or a
    label like "EQ-5D-5L" (which would otherwise become top level and swallow
    every later section)."""
    letters = [ch for ch in heading if ch.isalpha()]
    words = [w for w in heading.split() if any(ch.isalpha() for ch in w)]
    return len(letters) >= 4 and len(words) >= 2 and all(ch.isupper() for ch in letters)


@dataclass
class HeadingInfo:
    depth: int
    title: str
    numbered: bool = False


def assign_sections(
    is_heading: Sequence[bool], texts: Sequence[str]
) -> tuple[list[HeadingInfo | None], list[list[str]]]:
    """For blocks in reading order: each heading's depth, and every block's section path.

    The section path lists the headings a block sits under, outermost first; a
    heading's own path ends with itself.
    """
    headings: list[HeadingInfo | None] = []
    paths: list[list[str]] = []
    stack: list[HeadingInfo] = []
    last_numbered = 0

    for heading_flag, text in zip(is_heading, texts, strict=True):
        if not heading_flag:
            headings.append(None)
            paths.append([h.title for h in stack])
            continue
        title = clean_heading(text)
        depth = numbered_depth(title)
        numbered = depth is not None
        if depth is not None:
            last_numbered = depth
        elif is_capitalised(title):
            depth = last_numbered = 1
        else:
            depth = last_numbered + 1
        # A numbered heading never sits under an unnumbered one: "2.6.10" goes back
        # under "2.6", not under whatever label appeared since.
        while stack and (stack[-1].depth >= depth or (numbered and not stack[-1].numbered)):
            stack.pop()
        info = HeadingInfo(depth=depth, title=title, numbered=numbered)
        stack.append(info)
        headings.append(info)
        paths.append([h.title for h in stack])

    return headings, paths


def contiguous_ranges(text: str, reported: Sequence[tuple[int, int]]) -> list[tuple[int, int]]:
    """Character ranges for the pieces of a block that continues across pages.

    Docling reports one range per page, but when it joins the pieces it can drop a
    hyphen (a split word, joined without a space) or a bullet without shifting the
    later ranges. Their widths stay right; their offsets don't. So the pieces are
    laid out backwards from the end of the text: the last ends at the end; before
    each piece, a joining space (if the text has one there) is given to the piece
    before it. The result covers the whole text, in order, with no gaps or overlaps.
    """
    if len(reported) <= 1:
        return [(0, len(text))]
    ranges: list[tuple[int, int]] = []
    end = len(text)
    separator = 0
    for start_reported, end_reported in reversed(reported[1:]):
        width = max(end_reported - start_reported, 0)
        start = max(end - separator - width, 0)
        ranges.append((start, end))
        separator = 1 if start > 0 and text[start - 1] == " " else 0
        end = start
    ranges.append((0, end))
    return ranges[::-1]


def normalize_bbox(
    left: float,
    top: float,
    right: float,
    bottom: float,
    *,
    page_width: float,
    page_height: float,
    origin_bottom_left: bool,
    flip_height: float | None = None,
) -> BBox:
    """PDF points (either origin) to a 0-1 box with the origin at the top left.

    `flip_height` is the page height the parser used to produce bottom-left
    coordinates, when that differs from this page's height (see
    `app.ingest.parse._regions`). Values are clamped to the page: parsers
    sometimes report boxes a hair outside it.
    """
    if origin_bottom_left:
        flip = page_height if flip_height is None else flip_height
        top, bottom = flip - top, flip - bottom
    xs = sorted((left / page_width, right / page_width))
    ys = sorted((top / page_height, bottom / page_height))
    x0, x1 = (min(max(v, 0.0), 1.0) for v in xs)
    y0, y1 = (min(max(v, 0.0), 1.0) for v in ys)
    return BBox(x0=x0, y0=y0, x1=x1, y1=y1)


def with_sections(parsed: ParsedDocument) -> ParsedDocument:
    """The document with every heading's depth and every block's section path set.

    The parser calls this; re-chunking calls it again on the stored parse, so a
    change to the heading rules applies without parsing again.
    """
    blocks = [
        b.model_copy(update={"kind": BlockKind.CAPTION})
        if _is_table_caption(parsed.blocks, i)
        else b
        for i, b in enumerate(parsed.blocks)
    ]
    headings, paths = assign_sections(
        [b.kind in (BlockKind.HEADING, BlockKind.TITLE) for b in blocks],
        [b.text for b in blocks],
    )
    return parsed.model_copy(
        update={
            "blocks": [
                b.model_copy(update={"heading_depth": h.depth if h else None, "section_path": path})
                for b, h, path in zip(blocks, headings, paths, strict=True)
            ]
        }
    )


def _is_table_caption(blocks: Sequence[Block], position: int) -> bool:
    """A heading that reads like a table caption and is followed by a table."""
    block = blocks[position]
    return (
        block.kind in (BlockKind.HEADING, BlockKind.TITLE)
        and bool(_TABLE_CAPTION.match(block.text.strip()))
        and position + 1 < len(blocks)
        and blocks[position + 1].kind == BlockKind.TABLE
    )
