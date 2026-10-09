"""Section structure and box normalisation, using heading sequences from the real PDFs."""

import pytest
from pydantic import ValidationError

from app.ingest.models import BBox
from app.ingest.structure import (
    assign_sections,
    clean_heading,
    contiguous_ranges,
    is_capitalised,
    normalize_bbox,
    numbered_depth,
)


def _paths(headings: list[str | None]) -> list[list[str]]:
    """Section paths for a sequence where None is a body block and a string is a heading."""
    _, paths = assign_sections([h is not None for h in headings], [h or "body" for h in headings])
    return paths


@pytest.mark.parametrize(
    ("raw", "clean"),
    [
        ("--------------------------- RECENT MAJOR CHANGES ------", "RECENT MAJOR CHANGES"),
        ("  2.2.   About the product ", "2.2. About the product"),
        ("Section 3 Results", "Section 3 Results"),
        ("Co-administration", "Co-administration"),  # a single hyphen is text, not decoration
    ],
)
def test_clean_heading_strips_decoration_only(raw: str, clean: str) -> None:
    assert clean_heading(raw) == clean


@pytest.mark.parametrize(
    ("heading", "depth"),
    [
        ("5 WARNINGS AND PRECAUTIONS", 1),
        ("5.10 Never Share a MOUNJARO KwikPen", 2),
        ("2.4.1. Introduction", 3),
        ("Section 2 Methods", 1),
        ("Identified Risks", None),
        ("2005 annual report", None),  # a year is not a section number
        ("12/2025", None),
    ],
)
def test_numbered_depth(heading: str, depth: int | None) -> None:
    assert numbered_depth(heading) == depth


def test_capitalised_needs_real_words() -> None:
    assert is_capitalised("FULL PRESCRIBING INFORMATION")
    assert is_capitalised("WARNING: RISK OF THYROID C-CELL TUMORS")
    assert not is_capitalised("Identified Risks")
    assert not is_capitalised("GLP")  # too short: an acronym, not a heading


def test_numbered_and_unnumbered_headings_nest() -> None:
    # Scanned 2005 Byetta review, page 5 (headings as OCR read them).
    paths = _paths(
        [
            "1.2 Recommendation on Postmarketing Actions",
            "1.2.1 Risk Management Activity",
            "Identified Risks",
            None,
            "Proposed Risk Management Activity",
            None,
            "1.2.2 Required Phase 4 Commitments",
            None,
        ]
    )
    assert paths[3] == [
        "1.2 Recommendation on Postmarketing Actions",
        "1.2.1 Risk Management Activity",
        "Identified Risks",
    ]
    # A second unnumbered heading is a sibling of the first, not its child.
    assert paths[5][-2:] == ["1.2.1 Risk Management Activity", "Proposed Risk Management Activity"]
    assert paths[7] == [
        "1.2 Recommendation on Postmarketing Actions",
        "1.2.2 Required Phase 4 Commitments",
    ]


def test_capitalised_heading_after_a_table_of_contents_is_top_level() -> None:
    # Mounjaro label, page 2: contents lines are detected as headings, then the real ones follow.
    paths = _paths(
        [
            "13 NONCLINICAL TOXICOLOGY",
            "17 PATIENT COUNSELING INFORMATION",
            "WARNING: RISK OF THYROID C-CELL TUMORS",
            None,
            "1 INDICATIONS AND USAGE",
            None,
        ]
    )
    assert paths[3] == ["WARNING: RISK OF THYROID C-CELL TUMORS"]
    assert paths[5] == ["1 INDICATIONS AND USAGE"]


def test_body_before_any_heading_has_an_empty_path() -> None:
    assert _paths([None, "1 Introduction", None]) == [[], ["1 Introduction"], ["1 Introduction"]]


def test_heading_depths_are_reported() -> None:
    headings, _ = assign_sections(
        [True, True, False], ["2.3. Type of Application", "Development programme", "x"]
    )
    assert [h.depth if h else None for h in headings] == [2, 3, None]
    assert headings[0] is not None
    assert headings[0].title == "2.3. Type of Application"


def test_bottom_left_boxes_are_flipped_and_normalised() -> None:
    # US Letter page; a box 72 pt from the left and from the top, 1 inch square.
    box = normalize_bbox(
        72, 720, 144, 648, page_width=612, page_height=792, origin_bottom_left=True
    )
    assert box == BBox(x0=72 / 612, y0=72 / 792, x1=144 / 612, y1=144 / 792)


def test_top_left_boxes_are_only_normalised() -> None:
    box = normalize_bbox(0, 0, 306, 396, page_width=612, page_height=792, origin_bottom_left=False)
    assert box == BBox(x0=0, y0=0, x1=0.5, y1=0.5)


def test_boxes_slightly_outside_the_page_are_clamped() -> None:
    box = normalize_bbox(-2, 795, 615, -1, page_width=612, page_height=792, origin_bottom_left=True)
    assert box == BBox(x0=0, y0=0, x1=1, y1=1)


def test_bbox_rejects_inverted_corners() -> None:
    with pytest.raises(ValidationError):
        BBox(x0=0.5, y0=0, x1=0.4, y1=1)


def test_single_region_covers_the_whole_text() -> None:
    assert contiguous_ranges("one paragraph", [(0, 0)]) == [(0, 13)]


def test_continuation_joined_with_a_space() -> None:
    # "abc" + " " + "def": Docling reports (0, 3) and (4, 7), which happen to be right.
    assert contiguous_ranges("abc def", [(0, 3), (4, 7)]) == [(0, 4), (4, 7)]


def test_continuation_of_a_hyphen_split_word() -> None:
    # "abc-" + "def" -> "abcdef": Docling still reports (0, 4) and (5, 8).
    assert contiguous_ranges("abcdef", [(0, 4), (5, 8)]) == [(0, 3), (3, 6)]


def test_continuation_after_a_stripped_bullet() -> None:
    # "• item one" lost its bullet ("item one" + " " + "two"), but the first range wasn't updated.
    assert contiguous_ranges("item one two", [(0, 10), (11, 14)]) == [(0, 9), (9, 12)]


def test_three_pages_stay_end_to_end() -> None:
    ranges = contiguous_ranges("aaaaaa bbbbbb cccccc", [(0, 6), (7, 13), (14, 20)])
    assert ranges == [(0, 7), (7, 14), (14, 20)]


def test_continuation_box_is_unflipped_with_the_first_pages_height() -> None:
    # Portrait A4 page continuing onto a landscape page: Docling flipped the
    # continuation with the portrait height (842), not the landscape one (595).
    top_from_page_top = 100
    box = normalize_bbox(
        50,
        842 - top_from_page_top,
        150,
        842 - 2 * top_from_page_top,
        page_width=842,
        page_height=595,
        origin_bottom_left=True,
        flip_height=842,
    )
    assert box.y0 == pytest.approx(100 / 595)
    assert box.y1 == pytest.approx(200 / 595)
