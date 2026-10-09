"use client";

import { FileText, ListTree, X } from "lucide-react";
import dynamic from "next/dynamic";
import { useState } from "react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { Turn } from "@/lib/answers/chat";
import { documentName, pageLabel } from "@/lib/documents";
import { passagePages } from "@/lib/highlights";
import { cn } from "@/lib/utils";

import { EvidenceList } from "./evidence-list";

// pdf.js needs the browser: never rendered on the server.
const PdfViewer = dynamic(() => import("@/components/viewer/pdf-viewer"), {
  ssr: false,
  loading: () => <Skeleton className="m-3 aspect-8.5/11" />,
});

export type PanelState = {
  turnId: string;
  view: "source" | "evidence";
  n: number | null; // the passage shown in the source view
  focusKey: string; // new on every click, so the viewer jumps back to the passage
};

type Props = {
  turn: Turn | undefined;
  panel: PanelState | null;
  onView: (view: PanelState["view"]) => void;
  onOpen: (n: number) => void;
  onClose: () => void;
};

/**
 * The side panel: the PDF page behind a citation, with the passage
 * highlighted ("Source"), or every passage the model saw for that answer and
 * why it was found ("Evidence").
 */
export function SourcePanel({ turn, panel, onView, onOpen, onClose }: Props) {
  if (!turn || !panel) {
    return (
      <div className="text-muted-foreground flex h-full flex-col items-center justify-center gap-2 p-8 text-center text-sm">
        <FileText className="size-6" />
        <p>Select a citation to see the passage on its page.</p>
      </div>
    );
  }
  const cited = new Set(turn.done?.citations.map((c) => c.n) ?? []);
  const hit = panel.n === null ? undefined : turn.sources[panel.n - 1];
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-1 border-b px-2 py-1.5">
        <Tab
          active={panel.view === "source"}
          disabled={!hit}
          onClick={() => onView("source")}
        >
          <FileText data-icon="inline-start" /> Source
        </Tab>
        <Tab
          active={panel.view === "evidence"}
          onClick={() => onView("evidence")}
        >
          <ListTree data-icon="inline-start" /> Evidence ({turn.sources.length})
        </Tab>
        <Button
          variant="ghost"
          size="icon-sm"
          className="ml-auto"
          onClick={onClose}
          aria-label="Close panel"
        >
          <X />
        </Button>
      </div>
      {panel.view === "evidence" || !hit || panel.n === null ? (
        <EvidenceList
          query={turn.query}
          sources={turn.sources}
          cited={cited}
          selected={panel.n}
          onOpen={onOpen}
        />
      ) : (
        <>
          <PassageHeader n={panel.n} hit={hit} cited={cited.has(panel.n)} />
          <div className="min-h-0 flex-1">
            <PdfViewer
              documentId={hit.document_id}
              spans={hit.chunk.spans}
              focusKey={panel.focusKey}
            />
          </div>
        </>
      )}
    </div>
  );
}

function PassageHeader({
  n,
  hit,
  cited,
}: {
  n: number;
  hit: Turn["sources"][number];
  cited: boolean;
}) {
  const [showText, setShowText] = useState(false);
  const pages = passagePages(hit.chunk.spans);
  return (
    <div className="shrink-0 space-y-1 border-b px-4 py-2.5">
      <div className="flex items-start gap-2.5">
        <span className="bg-primary text-primary-foreground mt-0.5 inline-flex h-5 min-w-5 items-center justify-center rounded-md px-1 font-mono text-[0.7rem] font-medium tabular-nums">
          {n}
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-sm font-semibold">
            {documentName(hit.filename)}
          </p>
          <p className="text-muted-foreground truncate text-xs">
            {pageLabel(pages[0] ?? 1, pages.at(-1) ?? 1)}
            {cited ? " · cited in the answer" : " · found, not cited"}
            {hit.chunk.section_path.length > 0 &&
              ` · ${hit.chunk.section_path.join(" › ")}`}
          </p>
        </div>
        <Button
          variant="ghost"
          size="xs"
          onClick={() => setShowText((s) => !s)}
        >
          {showText ? "Hide text" : "Text"}
        </Button>
      </div>
      {showText && (
        <blockquote className="border-highlight bg-muted/40 max-h-48 overflow-y-auto border-l-2 py-1.5 pr-2 pl-3 text-xs leading-relaxed whitespace-pre-wrap">
          {hit.chunk.text}
        </blockquote>
      )}
    </div>
  );
}

function Tab({
  active,
  disabled,
  onClick,
  children,
}: {
  active: boolean;
  disabled?: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <Button
      variant={active ? "secondary" : "ghost"}
      size="sm"
      onClick={onClick}
      disabled={disabled}
      aria-pressed={active}
      className={cn(!active && "text-muted-foreground")}
    >
      {children}
    </Button>
  );
}
