"""What the parser hands to the chunker: our own types, not Docling's.

Every block knows where it came from: one or more regions, each a page and a box.
Boxes are normalised to the page, 0-1 with the origin at the top left, so they
map straight onto a rendered page of any size. Character ranges say which part of
the block's text each region covers (a paragraph can continue onto the next page).
"""

from enum import StrEnum
from typing import Self

from pydantic import BaseModel, ConfigDict, Field, model_validator


class BBox(BaseModel):
    model_config = ConfigDict(frozen=True)

    x0: float = Field(ge=0, le=1)
    y0: float = Field(ge=0, le=1)
    x1: float = Field(ge=0, le=1)
    y1: float = Field(ge=0, le=1)

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if self.x0 > self.x1 or self.y0 > self.y1:
            raise ValueError("bbox corners must be top-left then bottom-right")
        return self


class Region(BaseModel):
    """The part of a block that sits on one page."""

    page: int = Field(ge=1)
    bbox: BBox
    char_start: int = Field(ge=0)
    char_end: int = Field(ge=0)


class BlockKind(StrEnum):
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    TABLE = "table"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    FORMULA = "formula"
    CODE = "code"
    INDEX = "index"  # a table of contents: navigation, not content


class TableCell(BaseModel):
    row: int = Field(ge=0)
    col: int = Field(ge=0)
    row_span: int = Field(ge=1)
    col_span: int = Field(ge=1)
    text: str
    is_header: bool
    bbox: BBox | None  # the page is the table block's page


class Table(BaseModel):
    """A table's structure, for display. The block's text holds it as Markdown, for search."""

    n_rows: int = Field(ge=0)
    n_cols: int = Field(ge=0)
    cells: list[TableCell]


class Block(BaseModel):
    """One layout element (heading, paragraph, list item, table...) in reading order."""

    index: int = Field(ge=0)
    kind: BlockKind
    text: str
    regions: list[Region] = Field(min_length=1)
    heading_depth: int | None = None  # headings and titles only: 1 = top level
    # Headings this block sits under, outermost first; a heading includes itself.
    section_path: list[str] = []
    table: Table | None = None

    @model_validator(mode="after")
    def _regions_cover_text(self) -> Self:
        """Every character maps to exactly one region, in order: citation mapping relies on it.

        A table's Markdown can't be split by page, so each of its regions spans all of it.
        """
        length = len(self.text)
        if self.kind == BlockKind.TABLE:
            if any((r.char_start, r.char_end) != (0, length) for r in self.regions):
                raise ValueError("each region of a table must span the whole table text")
            return self
        edges = [(r.char_start, r.char_end) for r in self.regions]
        expected_start = 0
        for start, end in edges:
            if start != expected_start or end < start:
                raise ValueError(f"regions must cover the text end to end, got {edges}")
            expected_start = end
        if expected_start != length:
            raise ValueError(f"regions end at {expected_start}, text has {length} characters")
        return self


class Page(BaseModel):
    number: int = Field(ge=1)
    width_pt: float = Field(gt=0)
    height_pt: float = Field(gt=0)
    has_text_layer: bool  # False: scanned, so its text came from OCR


class ParsedDocument(BaseModel):
    pages: list[Page]
    blocks: list[Block]
    parser: str  # versions that produced it, so a re-parse can be justified
