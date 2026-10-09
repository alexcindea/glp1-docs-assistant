import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { Schemas } from "@/lib/api/client";

import { EvidenceList, whyFound } from "./evidence-list";

const hit = (
  id: number,
  ranks: Schemas["PathRanks"],
  page = 4,
): Schemas["SearchHit"] => ({
  document_id: "d1",
  filename: "fda-ozempic-label-2026.pdf",
  score: 0.03,
  ranks,
  chunk: {
    id,
    seq: id,
    kind: "text",
    text: `Passage ${id} text.`,
    section_path: ["2 DOSAGE", "2.2 Recommended Dosage"],
    token_count: 4,
    spans: [
      { page, x0: 0.1, y0: 0.1, x1: 0.9, y1: 0.2, char_start: 0, char_end: 5 },
    ],
  },
});

describe("EvidenceList", () => {
  it("lists every passage, marks the cited ones and opens one on click", () => {
    const onOpen = vi.fn();
    render(
      <EvidenceList
        query="What is the dose?"
        sources={[
          hit(1, { vector: 1, bm25: 3 }),
          hit(2, { vector: null, bm25: 1 }, 5),
        ]}
        cited={new Set([2])}
        selected={null}
        onOpen={onOpen}
      />,
    );

    expect(
      screen.getByText(/2 passages given to the model, 1 cited/),
    ).toBeInTheDocument();
    const second = screen.getByRole("button", { name: /^Open source 2:/ });
    expect(second).toHaveTextContent("Cited");
    expect(second).toHaveTextContent("p. 5");
    expect(
      screen.getByRole("button", { name: /^Open source 1:/ }),
    ).not.toHaveTextContent("Cited");

    fireEvent.click(second);

    expect(onOpen).toHaveBeenCalledWith(2);
  });
});

describe("whyFound", () => {
  it("says which search paths found a passage", () => {
    expect(whyFound({ vector: 2, bm25: 5 })).toBe(
      "Found by meaning search #2 and keyword search #5",
    );
    expect(whyFound({ vector: null, bm25: 1 })).toBe(
      "Found only by keyword search #1",
    );
    expect(whyFound({ vector: 7, bm25: null })).toBe(
      "Found only by meaning search #7",
    );
  });
});
