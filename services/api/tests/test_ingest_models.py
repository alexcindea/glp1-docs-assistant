"""ParsedDocument rules that citation mapping will rely on."""

import pytest
from pydantic import ValidationError

from app.ingest.models import BBox, Block, BlockKind, Region, Table

BOX = BBox(x0=0.1, y0=0.1, x1=0.9, y1=0.2)


def _block(kind: BlockKind, text: str, ranges: list[tuple[int, int]]) -> Block:
    return Block(
        index=0,
        kind=kind,
        text=text,
        regions=[
            Region(page=i + 1, bbox=BOX, char_start=s, char_end=e)
            for i, (s, e) in enumerate(ranges)
        ],
        table=Table(n_rows=0, n_cols=0, cells=[]) if kind == BlockKind.TABLE else None,
    )


def test_regions_covering_the_text_end_to_end_are_accepted() -> None:
    block = _block(BlockKind.PARAGRAPH, "abcdef", [(0, 3), (3, 6)])
    assert [r.page for r in block.regions] == [1, 2]


@pytest.mark.parametrize(
    "ranges",
    [
        [(0, 3), (4, 6)],  # gap
        [(0, 4), (3, 6)],  # overlap
        [(0, 3)],  # stops short
        [(1, 6)],  # doesn't start at 0
        [(3, 6), (0, 3)],  # out of order
    ],
)
def test_regions_with_gaps_or_overlaps_are_rejected(ranges: list[tuple[int, int]]) -> None:
    with pytest.raises(ValidationError):
        _block(BlockKind.PARAGRAPH, "abcdef", ranges)


def test_table_regions_each_span_the_whole_markdown() -> None:
    _block(BlockKind.TABLE, "| a |", [(0, 5), (0, 5)])
    with pytest.raises(ValidationError):
        _block(BlockKind.TABLE, "| a |", [(0, 2), (2, 5)])
