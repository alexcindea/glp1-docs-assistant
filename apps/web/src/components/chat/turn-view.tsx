"use client";

import { AlertTriangle, FileSearch, RotateCcw, SearchX } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import type { Turn } from "@/lib/answers/chat";
import type { Schemas } from "@/lib/api/client";
import { documentName, pageLabel } from "@/lib/documents";
import { cn } from "@/lib/utils";

import { AnswerText } from "./answer-text";
import { Feedback } from "./feedback";

type Props = {
  turn: Turn;
  selected: number | null; // passage shown in the side panel, if it is from this turn
  onSelect: (n: number) => void;
  onRetry: (question: string) => void;
  onEvidence: () => void; // show every passage the answer was given
};

export function TurnView(props: Props) {
  const { turn } = props;
  const rewritten = turn.query !== null && turn.query !== turn.question;
  return (
    <article
      className="space-y-3"
      aria-busy={turn.status === "searching" || turn.status === "answering"}
    >
      <h2 className="text-[0.95rem] leading-snug font-semibold">
        {turn.question}
      </h2>
      {rewritten && (
        <p className="text-muted-foreground text-xs">
          Searched for: {turn.query}
        </p>
      )}
      <TurnBody {...props} />
    </article>
  );
}

function TurnBody({ turn, selected, onSelect, onRetry, onEvidence }: Props) {
  switch (turn.status) {
    case "searching":
      return (
        <div className="space-y-2" role="status">
          <p className="text-muted-foreground text-sm">
            Searching the documents…
          </p>
          <Skeleton className="h-4 w-11/12" />
          <Skeleton className="h-4 w-8/12" />
        </div>
      );
    case "answering":
      return turn.text ? (
        <AnswerText
          text={turn.text}
          passageCount={turn.sources.length}
          selected={selected}
          onCite={onSelect}
          streaming
        />
      ) : (
        <p className="text-muted-foreground text-sm" role="status">
          Reading {turn.sources.length} passages…
        </p>
      );
    case "answered":
      return (
        <div className="space-y-3">
          <AnswerText
            text={turn.text}
            passageCount={turn.sources.length}
            selected={selected}
            onCite={onSelect}
          />
          <CitedSources turn={turn} selected={selected} onSelect={onSelect} />
          <Meta
            done={turn.done}
            sources={turn.sources.length}
            onEvidence={onEvidence}
          />
        </div>
      );
    case "refused":
      return (
        <div className="space-y-2">
          <RefusalCard turn={turn} selected={selected} onSelect={onSelect} />
          <Meta
            done={turn.done}
            sources={turn.sources.length}
            onEvidence={onEvidence}
          />
        </div>
      );
    case "failed":
      return (
        <div
          className="border-destructive/30 bg-destructive/5 flex items-start gap-3 rounded-lg border p-3 text-sm"
          role="alert"
        >
          <AlertTriangle className="text-destructive mt-0.5 size-4 shrink-0" />
          <div className="flex-1 space-y-2">
            <p>{turn.error?.message ?? "Something went wrong."}</p>
            <Button
              size="sm"
              variant="outline"
              onClick={() => onRetry(turn.question)}
            >
              <RotateCcw data-icon="inline-start" /> Ask again
            </Button>
          </div>
        </div>
      );
    case "stopped":
      return (
        <p className="text-muted-foreground text-sm" role="status">
          Stopped before the answer was checked, so nothing from it is shown.
        </p>
      );
  }
}

function CitedSources({
  turn,
  selected,
  onSelect,
}: Pick<Props, "turn" | "selected" | "onSelect">) {
  const citations = turn.done?.citations ?? [];
  if (!citations.length) return null;
  return (
    <ul className="flex flex-wrap gap-1.5" aria-label="Sources cited">
      {citations.map((c) => (
        <li key={c.n}>
          <SourceButton
            n={c.n}
            name={documentName(c.filename)}
            pages={pageLabel(c.page_start, c.page_end)}
            active={selected === c.n}
            onClick={() => onSelect(c.n)}
          />
        </li>
      ))}
    </ul>
  );
}

function RefusalCard({
  turn,
  selected,
  onSelect,
}: Pick<Props, "turn" | "selected" | "onSelect">) {
  const refusal = turn.done?.refusal;
  if (!refusal) return null;
  const outOfScope = refusal.reason === "out_of_scope";
  const Icon = outOfScope ? FileSearch : SearchX;
  return (
    <div className="bg-muted/40 space-y-3 rounded-lg border p-3" role="status">
      <div className="flex items-start gap-2.5">
        <Icon className="text-muted-foreground mt-0.5 size-4 shrink-0" />
        <div className="space-y-0.5">
          <p className="text-sm font-medium">
            {outOfScope
              ? "Outside what these documents can answer"
              : "Not found in these documents"}
          </p>
          <p className="text-muted-foreground text-sm">{refusal.message}</p>
        </div>
      </div>
      {refusal.closest.length > 0 && (
        <div className="space-y-1.5">
          <p className="text-muted-foreground text-xs font-medium">
            Closest passages
          </p>
          <ul className="space-y-1">
            {refusal.closest.map((hit, i) => (
              <li key={hit.chunk.id}>
                <ClosestPassage
                  hit={hit}
                  active={selected === i + 1}
                  onClick={() => onSelect(i + 1)}
                />
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function ClosestPassage({
  hit,
  active,
  onClick,
}: {
  hit: Schemas["SearchHit"];
  active: boolean;
  onClick: () => void;
}) {
  const pages = hit.chunk.spans.map((s) => s.page);
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "bg-background hover:border-primary/50 w-full rounded-md border px-2.5 py-1.5 text-left text-sm transition-colors",
        active && "border-primary ring-primary ring-1",
      )}
    >
      <span className="font-medium">{documentName(hit.filename)}</span>
      <span className="text-muted-foreground">
        {" "}
        · {pageLabel(Math.min(...pages), Math.max(...pages))}
      </span>
      <span className="text-muted-foreground block truncate text-xs">
        {hit.chunk.section_path.at(-1) ?? hit.chunk.text.slice(0, 80)}
      </span>
    </button>
  );
}

function SourceButton({
  n,
  name,
  pages,
  active,
  onClick,
}: {
  n: number;
  name: string;
  pages: string;
  active: boolean;
  onClick: () => void;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={active}
      className={cn(
        "hover:border-primary/50 inline-flex max-w-full items-center gap-1.5 rounded-md border px-2 py-1 text-xs transition-colors",
        active ? "border-primary bg-primary/5" : "bg-background",
      )}
    >
      <span className="font-mono font-medium tabular-nums">{n}</span>
      <span className="truncate">{name}</span>
      <span className="text-muted-foreground shrink-0">{pages}</span>
    </button>
  );
}

function Meta({
  done,
  sources,
  onEvidence,
}: {
  done: Schemas["DoneEvent"] | null;
  sources: number;
  onEvidence: () => void;
}) {
  if (!done) return null;
  const seconds = (done.timings.total_ms / 1000).toFixed(1);
  const cost =
    done.usage.cost_usd === null ? null : `$${done.usage.cost_usd.toFixed(4)}`;
  return (
    <div className="space-y-2">
      <p className="text-muted-foreground flex flex-wrap items-center gap-x-1.5 text-xs">
        {sources > 0 && (
          <>
            <button
              type="button"
              onClick={onEvidence}
              className="hover:text-foreground underline decoration-dotted underline-offset-2"
            >
              Evidence: {sources} passages
            </button>
            <span aria-hidden>·</span>
          </>
        )}
        <span>
          {[`${seconds} s`, cost, done.model].filter(Boolean).join(" · ")}
        </span>
      </p>
      {done.answer_id && <Feedback answerId={done.answer_id} />}
    </div>
  );
}
