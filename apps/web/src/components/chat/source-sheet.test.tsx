import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it } from "vitest";

import { SourceSheet } from "./source-sheet";

function Page() {
  const [opener, setOpener] = useState<HTMLElement | null>(null);
  const open = opener !== null;
  return (
    <>
      <button type="button" onClick={(e) => setOpener(e.currentTarget)}>
        Show source 3
      </button>
      {open && (
        <SourceSheet onClose={() => setOpener(null)} opener={opener}>
          <p>Passage text</p>
        </SourceSheet>
      )}
    </>
  );
}

describe("SourceSheet", () => {
  it("takes focus when opened, closes on Escape and gives focus back to the citation", () => {
    render(<Page />);
    const citation = screen.getByRole("button", { name: "Show source 3" });
    citation.focus();

    fireEvent.click(citation);
    const sheet = screen.getByRole("dialog", { name: "Sources" });

    expect(sheet).toHaveAttribute("aria-modal", "true");
    expect(sheet).toHaveFocus();

    fireEvent.keyDown(sheet, { key: "Escape" });

    expect(screen.queryByRole("dialog")).toBeNull();
    expect(citation).toHaveFocus();
  });
});
