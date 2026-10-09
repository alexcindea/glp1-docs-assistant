import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { AnswerText } from "./answer-text";

function renderAnswer(text: string, onCite = vi.fn()) {
  const view = render(
    <AnswerText text={text} passageCount={3} selected={2} onCite={onCite} />,
  );
  return { ...view, onCite };
}

describe("AnswerText", () => {
  it("renders each citation of a real passage as a button that shows it", () => {
    const { onCite } = renderAnswer(
      "The dose is **2 mg** [2][3]; see also [9].",
    );

    fireEvent.click(screen.getByRole("button", { name: "Show source 3" }));

    expect(onCite).toHaveBeenCalledWith(3);
    expect(
      screen.getByRole("button", { name: "Show source 2" }),
    ).toHaveAttribute("aria-pressed", "true");
    expect(screen.queryByRole("button", { name: "Show source 9" })).toBeNull();
    expect(screen.getByText(/see also \[9\]/)).toBeInTheDocument();
    expect(screen.getByText("2 mg").tagName).toBe("STRONG");
  });

  it("never renders links, images or HTML from the model", () => {
    const { container } = renderAnswer(
      'Read [the label](https://evil.test) ![x](https://evil.test/x.png) <img src="x" onerror="alert(1)">',
    );

    expect(container.querySelector("a")).toBeNull();
    expect(container.querySelector("img")).toBeNull();
    expect(screen.getByText(/the label/)).toBeInTheDocument();
  });
});
