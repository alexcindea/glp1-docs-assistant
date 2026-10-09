import { describe, expect, it } from "vitest";

import { highlightBoxes, passagePages, type Span } from "./highlights";

const span = (
  page: number,
  x0: number,
  y0: number,
  x1: number,
  y1: number,
): Span => ({
  page,
  x0,
  y0,
  x1,
  y1,
  char_start: 0,
  char_end: 10,
});

describe("highlightBoxes", () => {
  it("places a box by percentage of the page, with a little padding", () => {
    const [box] = highlightBoxes([span(4, 0.1, 0.2, 0.6, 0.25)], 4);

    expect(box?.left).toBeCloseTo(9.7);
    expect(box?.top).toBeCloseTo(19.7);
    expect(box?.width).toBeCloseTo(50.6);
    expect(box?.height).toBeCloseTo(5.6);
  });

  it("draws only the spans on the page shown", () => {
    const spans = [span(4, 0.1, 0.8, 0.9, 0.95), span(5, 0.1, 0.05, 0.9, 0.2)];

    expect(highlightBoxes(spans, 4)).toHaveLength(1);
    expect(highlightBoxes(spans, 5)).toHaveLength(1);
    expect(highlightBoxes(spans, 6)).toEqual([]);
  });

  it("keeps padded boxes on the page", () => {
    const [box] = highlightBoxes([span(1, 0, 0, 1, 1)], 1);

    expect(box).toEqual({ left: 0, top: 0, width: 100, height: 100 });
  });

  it("tolerates a box stored with its corners swapped", () => {
    const [box] = highlightBoxes([span(1, 0.6, 0.25, 0.1, 0.2)], 1);

    expect(box?.left).toBeCloseTo(9.7);
    expect(box?.width).toBeCloseTo(50.6);
  });
});

describe("passagePages", () => {
  it("lists each page once, in order", () => {
    const spans = [
      span(5, 0, 0, 1, 1),
      span(4, 0, 0, 1, 1),
      span(5, 0, 0, 1, 1),
    ];

    expect(passagePages(spans)).toEqual([4, 5]);
  });
});
