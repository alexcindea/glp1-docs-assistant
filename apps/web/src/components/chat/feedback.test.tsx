import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { Feedback } from "./feedback";

afterEach(() => vi.unstubAllGlobals());

function stubFetch(status = 200) {
  const calls: { url: string; body: unknown }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init: RequestInit) => {
      calls.push({ url, body: JSON.parse(String(init.body)) });
      return new Response("{}", { status });
    }),
  );
  return calls;
}

describe("Feedback", () => {
  it("saves a thumbs down, then its comment, against the answer", async () => {
    const calls = stubFetch();
    render(<Feedback answerId="a1" />);

    fireEvent.click(screen.getByRole("button", { name: "Bad answer" }));
    fireEvent.change(screen.getByLabelText("What was wrong?"), {
      target: { value: "  Wrong page.  " },
    });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));

    await waitFor(() =>
      expect(screen.getByText("Thanks, saved.")).toBeInTheDocument(),
    );
    expect(calls).toEqual([
      {
        url: "/api/answers/a1/feedback",
        body: { rating: "down", comment: null },
      },
      {
        url: "/api/answers/a1/feedback",
        body: { rating: "down", comment: "Wrong page." },
      },
    ]);
    expect(screen.getByRole("button", { name: "Bad answer" })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });

  it("says when feedback could not be saved", async () => {
    stubFetch(500);
    render(<Feedback answerId="a1" />);

    fireEvent.click(screen.getByRole("button", { name: "Good answer" }));

    await waitFor(() =>
      expect(screen.getByText(/Couldn.t save/)).toBeInTheDocument(),
    );
  });
});
