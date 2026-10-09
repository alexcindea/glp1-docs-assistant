import type { Schemas } from "@/lib/api/client";

import type { AnswerEvent } from "./stream";

export type TurnStatus =
  | "searching" // question sent, no passages yet
  | "answering" // passages found, answer streaming
  | "answered"
  | "refused" // the documents don't answer it, or it is out of scope
  | "failed" // the model or the network failed
  | "stopped"; // the user stopped it

/** One question and everything known about its answer so far. */
export interface Turn {
  id: string;
  question: string;
  status: TurnStatus;
  query: string | null; // what was searched for (a follow-up is rewritten)
  sources: Schemas["SearchHit"][]; // passage n is sources[n - 1]
  text: string; // the answer as streamed, then as checked by the API
  done: Schemas["DoneEvent"] | null;
  error: { code: string; message: string } | null;
}

export function startTurn(id: string, question: string): Turn {
  return {
    id,
    question,
    status: "searching",
    query: null,
    sources: [],
    text: "",
    done: null,
    error: null,
  };
}

/**
 * The turn after one event. `done` is authoritative: its answer replaces the
 * streamed text (markers normalised, invalid ones dropped), and a refusal
 * clears text that streamed before the API found it cites nothing.
 */
export function applyEvent(turn: Turn, event: AnswerEvent): Turn {
  switch (event.kind) {
    case "sources":
      return {
        ...turn,
        status: "answering",
        query: event.query,
        sources: event.passages,
      };
    case "delta":
      return { ...turn, text: turn.text + event.text };
    case "done":
      return {
        ...turn,
        status: event.refusal ? "refused" : "answered",
        text: event.refusal ? "" : event.answer,
        done: event,
      };
    case "error":
      return {
        ...turn,
        status: "failed",
        error: { code: event.code, message: event.message },
      };
  }
}

/** The turn when the stream broke off without an ending: stopped by the user, or failed. */
export function endTurn(
  turn: Turn,
  reason: "stopped" | { code: string; message: string },
): Turn {
  if (turn.status !== "searching" && turn.status !== "answering") return turn;
  if (reason === "stopped") return { ...turn, status: "stopped" };
  return { ...turn, status: "failed", error: reason };
}

export const HISTORY_TURNS = 5;
const ANSWER_CHARS = 8000; // the API's limit per earlier answer

/**
 * The earlier turns to send with a new question, oldest first: what makes
 * "And for Mounjaro?" answerable. Unfinished and failed turns are left out.
 */
export function historyFor(turns: Turn[]): Schemas["Turn"][] {
  return turns
    .filter((t) => t.status === "answered" || t.status === "refused")
    .slice(-HISTORY_TURNS)
    .map((t) => ({
      question: t.question,
      answer: t.text.slice(0, ANSWER_CHARS),
    }));
}
