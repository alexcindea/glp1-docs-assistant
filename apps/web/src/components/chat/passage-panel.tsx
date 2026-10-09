"use client";

import { FileText, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import type { Schemas } from "@/lib/api/client";
import { documentName, pageLabel } from "@/lib/documents";

type Props = {
  passage: { n: number; hit: Schemas["SearchHit"]; cited: boolean } | null;
  onClose: () => void;
};

/** The passage behind a citation: which document, which pages, which section, what it says. */
export function PassagePanel({ passage, onClose }: Props) {
  if (!passage) {
    return (
      <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 p-8 text-center text-sm">
        <FileText className="size-6" />
        <p>Select a citation to see the passage it came from.</p>
      </div>
    );
  }
  const { n, hit, cited } = passage;
  const pages = hit.chunk.spans.map((s) => s.page);
  return (
    <div className="flex h-full min-h-0 flex-col">
      <header className="flex items-start gap-3 border-b px-4 py-3">
        <span className="bg-primary text-primary-foreground mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-md px-1 font-mono text-[0.7rem] font-medium tabular-nums">
          {n}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">
            {documentName(hit.filename)}
          </p>
          <p className="text-muted-foreground text-xs">
            {pageLabel(Math.min(...pages), Math.max(...pages))}
            {cited ? " · cited in the answer" : " · found, not cited"}
          </p>
        </div>
        <Button
          variant="ghost"
          size="icon"
          onClick={onClose}
          aria-label="Close passage"
        >
          <X />
        </Button>
      </header>
      <div className="min-h-0 flex-1 space-y-3 overflow-y-auto px-4 py-3">
        {hit.chunk.section_path.length > 0 && (
          <p className="text-muted-foreground text-xs">
            {hit.chunk.section_path.join(" › ")}
          </p>
        )}
        <blockquote className="border-primary/60 bg-muted/40 border-l-2 py-2 pr-2 pl-3 text-sm leading-relaxed whitespace-pre-wrap">
          {hit.chunk.text}
        </blockquote>
      </div>
    </div>
  );
}
