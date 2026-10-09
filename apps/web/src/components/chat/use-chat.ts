"use client";

import { useCallback, useRef, useState } from "react";

import {
  applyEvent,
  endTurn,
  historyFor,
  startTurn,
  type Turn,
} from "@/lib/answers/chat";
import { AnswerRequestError, streamAnswer } from "@/lib/answers/stream";

const BROKE_OFF = {
  code: "incomplete",
  message: "The answer stopped before it finished. Try asking again.",
};
const OFFLINE = {
  code: "network",
  message:
    "Lost the connection to the server. Check it is running and try again.",
};

/**
 * The conversation: turns in order, the one being answered last. It lives in
 * this page only (a refresh starts over); each answer is saved by the API.
 */
export function useChat() {
  const [turns, setTurns] = useState<Turn[]>([]);
  const running = useRef<AbortController | null>(null);
  const last = turns.at(-1);
  const busy = last?.status === "searching" || last?.status === "answering";

  const ask = useCallback(
    async (question: string, documentIds: string[]) => {
      if (running.current) return; // one question at a time
      const id = crypto.randomUUID();
      const abort = new AbortController();
      running.current = abort;
      const history = historyFor(turns); // the turns before this one
      setTurns((current) => [...current, startTurn(id, question)]);
      const update = (change: (turn: Turn) => Turn) =>
        setTurns((current) =>
          current.map((t) => (t.id === id ? change(t) : t)),
        );
      try {
        const events = streamAnswer(
          {
            question,
            history,
            document_ids: documentIds.length ? documentIds : null,
          },
          { signal: abort.signal },
        );
        for await (const event of events) update((t) => applyEvent(t, event));
        update((t) => endTurn(t, BROKE_OFF)); // no-op when it ended properly
      } catch (error) {
        if (abort.signal.aborted) update((t) => endTurn(t, "stopped"));
        else if (error instanceof AnswerRequestError)
          update((t) =>
            endTurn(t, { code: error.code, message: error.message }),
          );
        else update((t) => endTurn(t, OFFLINE));
      } finally {
        running.current = null;
      }
    },
    [turns],
  );

  const stop = useCallback(() => running.current?.abort(), []);

  return { turns, busy, ask, stop };
}
