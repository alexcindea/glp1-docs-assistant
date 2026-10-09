"use client";

import { ArrowUp, Square } from "lucide-react";
import { useState, type FormEvent, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { Textarea } from "@/components/ui/textarea";

const MAX_CHARS = 2000; // the API's limit

type Props = {
  busy: boolean;
  onAsk: (question: string) => void;
  onStop: () => void;
  filter: React.ReactNode; // the document filter, shown beside the send button
};

/** Enter sends, Shift+Enter starts a new line; while answering, the button stops it. */
export function QuestionForm({ busy, onAsk, onStop, filter }: Props) {
  const [question, setQuestion] = useState("");
  const trimmed = question.trim();

  function submit(event?: FormEvent) {
    event?.preventDefault();
    if (busy || !trimmed) return;
    onAsk(trimmed);
    setQuestion("");
  }

  function onKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (
      event.key === "Enter" &&
      !event.shiftKey &&
      !event.nativeEvent.isComposing
    ) {
      event.preventDefault();
      submit();
    }
  }

  return (
    <form
      onSubmit={submit}
      className="bg-background focus-within:ring-ring/30 rounded-xl border p-2 shadow-sm focus-within:ring-2"
    >
      <label htmlFor="question" className="sr-only">
        Your question
      </label>
      <Textarea
        id="question"
        value={question}
        onChange={(e) => setQuestion(e.target.value)}
        onKeyDown={onKeyDown}
        placeholder="Ask a question about the documents…"
        maxLength={MAX_CHARS}
        rows={2}
        className="max-h-40 min-h-12 resize-none border-0 shadow-none focus-visible:ring-0"
      />
      <div className="flex items-center justify-between gap-2 pt-1">
        {filter}
        {busy ? (
          <Button
            type="button"
            size="icon"
            variant="secondary"
            onClick={onStop}
            aria-label="Stop answering"
          >
            <Square className="fill-current" />
          </Button>
        ) : (
          <Button
            type="submit"
            size="icon"
            disabled={!trimmed}
            aria-label="Ask"
          >
            <ArrowUp />
          </Button>
        )}
      </div>
    </form>
  );
}
