"use client";

import { createContext, useContext, type ComponentProps } from "react";
import Markdown, { type Components, type ExtraProps } from "react-markdown";

import { citedPassage, linkCitations } from "@/lib/answers/citations";
import { cn } from "@/lib/utils";

type Props = {
  text: string;
  passageCount: number;
  selected: number | null;
  onCite: (n: number) => void;
  streaming?: boolean; // shows a cursor at the end of the text
};

type Citing = Pick<Props, "passageCount" | "selected" | "onCite">;

const CitingContext = createContext<Citing>({
  passageCount: 0,
  selected: null,
  onCite: () => undefined,
});

/**
 * The answer as Markdown, each `[n]` a button that shows passage n.
 *
 * Model output is untrusted: raw HTML is never rendered (react-markdown's
 * default), images are dropped, and links other than citations of a real
 * passage are shown as plain text, so an answer can't load or link to
 * anything outside the app.
 */
export function AnswerText({
  text,
  passageCount,
  selected,
  onCite,
  streaming = false,
}: Props) {
  return (
    <div
      className="prose-answer text-[0.95rem] leading-relaxed"
      data-streaming={streaming || undefined}
    >
      <CitingContext value={{ passageCount, selected, onCite }}>
        <Markdown components={COMPONENTS} disallowedElements={["img"]}>
          {linkCitations(text, passageCount)}
        </Markdown>
      </CitingContext>
    </div>
  );
}

/**
 * One component for every render: react-markdown uses it as an element type,
 * so a new function each render would remount every chip (on every streamed
 * word), losing focus. State comes through context instead.
 */
function CitationLink({ href, children }: ComponentProps<"a"> & ExtraProps) {
  const { passageCount, selected, onCite } = useContext(CitingContext);
  const n = citedPassage(href);
  if (n === null || n < 1 || n > passageCount) return <>{children}</>;
  return (
    <button
      type="button"
      onClick={() => onCite(n)}
      aria-label={`Show source ${n}`}
      aria-pressed={selected === n}
      className={cn(
        "mx-0.5 inline-flex h-5 min-w-5 -translate-y-px items-center justify-center rounded-md border px-1 align-middle font-mono text-[0.7rem] font-medium tabular-nums transition-colors",
        selected === n
          ? "border-primary bg-primary text-primary-foreground"
          : "border-border bg-muted text-foreground hover:border-primary/60 hover:bg-primary/10",
      )}
    >
      {n}
    </button>
  );
}

const COMPONENTS: Components = { a: CitationLink };
