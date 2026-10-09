"""Draw parsed blocks onto their PDF pages, to check provenance by eye.

Usage (from the repo root):
    uv run python -m app.ingest.parse corpus/x.pdf --pages 3-8 --out parsed.json
    uv run python scripts/render_provenance.py corpus/x.pdf parsed.json out-dir/

Writes one PNG per page: every block's box, coloured by kind and numbered by
block index. A box that misses its text means provenance is wrong somewhere.
"""

from __future__ import annotations

import argparse
from pathlib import Path

import pypdfium2 as pdfium
from app.ingest.models import BlockKind, ParsedDocument
from PIL import Image, ImageDraw

COLOURS = {
    BlockKind.TITLE: (180, 0, 180),
    BlockKind.HEADING: (220, 30, 30),
    BlockKind.PARAGRAPH: (30, 90, 220),
    BlockKind.LIST_ITEM: (0, 150, 150),
    BlockKind.TABLE: (0, 160, 0),
    BlockKind.CAPTION: (230, 130, 0),
    BlockKind.FOOTNOTE: (120, 120, 120),
    BlockKind.FORMULA: (120, 60, 0),
    BlockKind.CODE: (120, 60, 0),
    BlockKind.INDEX: (150, 150, 0),
}
SCALE = 1.5  # 108 dpi: readable, small files


def render(pdf_path: Path, parsed: ParsedDocument, out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = pdfium.PdfDocument(pdf_path)
    written: list[Path] = []
    try:
        for page in parsed.pages:
            image = pdf[page.number - 1].render(scale=SCALE).to_pil().convert("RGB")
            overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            w, h = image.size
            for block in parsed.blocks:
                colour = COLOURS[block.kind]
                for region in block.regions:
                    if region.page != page.number:
                        continue
                    box = (
                        region.bbox.x0 * w,
                        region.bbox.y0 * h,
                        region.bbox.x1 * w,
                        region.bbox.y1 * h,
                    )
                    draw.rectangle(box, outline=(*colour, 255), fill=(*colour, 28), width=2)
                    draw.text((box[0] + 2, box[1] + 1), str(block.index), fill=(*colour, 255))
                if block.table and any(r.page == page.number for r in block.regions):
                    for cell in block.table.cells:
                        if cell.bbox:
                            draw.rectangle(
                                (
                                    cell.bbox.x0 * w,
                                    cell.bbox.y0 * h,
                                    cell.bbox.x1 * w,
                                    cell.bbox.y1 * h,
                                ),
                                outline=(*colour, 140),
                                width=1,
                            )
            path = out_dir / f"{pdf_path.stem}-p{page.number}.png"
            Image.alpha_composite(image.convert("RGBA"), overlay).convert("RGB").save(path)
            written.append(path)
    finally:
        pdf.close()
    return written


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("pdf", type=Path)
    parser.add_argument("parsed", type=Path, help="ParsedDocument JSON from app.ingest.parse --out")
    parser.add_argument("out_dir", type=Path)
    args = parser.parse_args()
    parsed = ParsedDocument.model_validate_json(args.parsed.read_text(encoding="utf-8"))
    for path in render(args.pdf, parsed, args.out_dir):
        print(path)


if __name__ == "__main__":
    main()
