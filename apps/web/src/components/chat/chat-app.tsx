"use client";

import { useEffect, useRef, useState } from "react";

import type { DocumentOut } from "@/lib/documents";

import { DocumentFilter } from "./document-filter";
import { PassagePanel } from "./passage-panel";
import { QuestionForm } from "./question-form";
import { TurnView } from "./turn-view";
import { useChat } from "./use-chat";

type Selection = { turnId: string; n: number };

type Props = {
  documents: DocumentOut[];
  documentsError: string | null;
};

/**
 * The chat: questions and answers on the left; on the right, the passage
 * behind the citation last clicked. On narrow screens the passage opens as a
 * full-screen sheet instead.
 */
export function ChatApp({ documents, documentsError }: Props) {
  const { turns, busy, ask, stop } = useChat();
  const [filter, setFilter] = useState<string[]>([]);
  const [selection, setSelection] = useState<Selection | null>(null);
  const bottom = useRef<HTMLDivElement>(null);

  const lastText = turns.at(-1)?.text.length ?? 0;
  useEffect(() => {
    bottom.current?.scrollIntoView({ block: "end" });
  }, [turns.length, lastText]);

  const selectedTurn = selection
    ? turns.find((t) => t.id === selection.turnId)
    : undefined;
  const hit = selection && selectedTurn?.sources[selection.n - 1];
  const passage =
    selection && hit
      ? {
          n: selection.n,
          hit,
          cited:
            selectedTurn.done?.citations.some((c) => c.n === selection.n) ??
            false,
        }
      : null;

  const panel = (
    <PassagePanel passage={passage} onClose={() => setSelection(null)} />
  );

  return (
    <div className="grid min-h-0 flex-1 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
      <section className="flex min-h-0 flex-col" aria-label="Conversation">
        <div className="min-h-0 flex-1 overflow-y-auto">
          <div className="mx-auto w-full max-w-2xl space-y-8 px-4 py-6">
            {documentsError && (
              <p
                className="border-destructive/30 bg-destructive/5 rounded-md border p-3 text-sm"
                role="alert"
              >
                {documentsError} Answers may fail until it is back.
              </p>
            )}
            {turns.length === 0 ? (
              <EmptyState
                count={documents.filter((d) => d.status === "ready").length}
              />
            ) : (
              turns.map((turn) => (
                <TurnView
                  key={turn.id}
                  turn={turn}
                  selected={selection?.turnId === turn.id ? selection.n : null}
                  onSelect={(n) => setSelection({ turnId: turn.id, n })}
                  onRetry={(question) => void ask(question, filter)}
                />
              ))
            )}
            <div ref={bottom} />
          </div>
        </div>
        <div className="mx-auto w-full max-w-2xl px-4 pb-4">
          <QuestionForm
            busy={busy}
            onAsk={(question) => void ask(question, filter)}
            onStop={stop}
            filter={
              <DocumentFilter
                documents={documents}
                selected={filter}
                onChange={setFilter}
              />
            }
          />
        </div>
      </section>
      <aside
        className="bg-muted/20 hidden min-h-0 border-l lg:block"
        aria-label="Source passage"
      >
        {panel}
      </aside>
      {passage && (
        <div
          className="bg-background fixed inset-0 z-40 flex flex-col lg:hidden"
          role="dialog"
          aria-label="Source passage"
        >
          {panel}
        </div>
      )}
    </div>
  );
}

function EmptyState({ count }: { count: number }) {
  return (
    <div className="space-y-2 pt-[12vh] text-center">
      <h1 className="text-xl font-semibold">Ask the documents</h1>
      <p className="text-muted-foreground mx-auto max-w-md text-sm">
        {count > 0
          ? `Answers come from ${count} document${count === 1 ? "" : "s"}, and every claim links to the passage it came from.`
          : "No documents are ready yet. Upload a PDF to start."}
      </p>
    </div>
  );
}
