import type { Schemas } from "@/lib/api/client";

export type Span = Schemas["SpanOut"];

/** Where to draw one highlight on a page, as percentages of the page's size. */
export type HighlightBox = {
  left: number;
  top: number;
  width: number;
  height: number;
};

/** Breathing room around a line, as a fraction of the page: text sits tight in its box. */
const PAD = 0.003;

/**
 * Boxes to draw over page `page` for a passage's spans.
 *
 * Spans store boxes normalised to the page (0-1, origin top left), measured on
 * the page as displayed: rotation and crop applied, as pdf.js renders it. So a
 * box is placed by percentage over the rendered page, at any zoom, with no
 * PDF coordinates involved. A passage across a page break has spans on both
 * pages; each page draws only its own.
 */
export function highlightBoxes(spans: Span[], page: number): HighlightBox[] {
  return spans
    .filter((span) => span.page === page)
    .map((span) => {
      const x0 = clamp(Math.min(span.x0, span.x1) - PAD);
      const y0 = clamp(Math.min(span.y0, span.y1) - PAD);
      const x1 = clamp(Math.max(span.x0, span.x1) + PAD);
      const y1 = clamp(Math.max(span.y0, span.y1) + PAD);
      return {
        left: x0 * 100,
        top: y0 * 100,
        width: (x1 - x0) * 100,
        height: (y1 - y0) * 100,
      };
    });
}

/** The pages a passage covers, in order. */
export function passagePages(spans: Span[]): number[] {
  return [...new Set(spans.map((span) => span.page))].sort((a, b) => a - b);
}

function clamp(value: number): number {
  return Math.min(1, Math.max(0, value));
}
