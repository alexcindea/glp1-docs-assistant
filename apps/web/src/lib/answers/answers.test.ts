// @vitest-environment node
import { describe, expect, it } from "vitest";

import type { Schemas } from "@/lib/api/client";

import { applyEvent, endTurn, historyFor, startTurn, type Turn } from "./chat";
import { citedPassage, linkCitations } from "./citations";
import {
  AnswerRequestError,
  parseEvent,
  streamAnswer,
  type AnswerEvent,
} from "./stream";

const hit = (text: string): Schemas["SearchHit"] => ({
  document_id: "d1",
  filename: "label.pdf",
  score: 0.03,
  ranks: { vector: 1, bm25: 2 },
  chunk: {
    id: 1,
    seq: 0,
    kind: "text",
    text,
    section_path: ["2 DOSAGE"],
    token_count: 5,
    spans: [
      {
        page: 4,
        x0: 0.1,
        y0: 0.2,
        x1: 0.9,
        y1: 0.25,
        char_start: 0,
        char_end: 5,
      },
    ],
  },
});

const usage = {
  input_tokens: 10,
  cached_input_tokens: 0,
  output_tokens: 5,
  reasoning_tokens: 0,
  cost_usd: 0.006,
};
const timings = {
  rewrite_ms: null,
  retrieval_ms: 200,
  first_token_ms: 2000,
  total_ms: 3000,
};

const done = (
  overrides: Partial<Schemas["DoneEvent"]> = {},
): Schemas["DoneEvent"] => ({
  kind: "done",
  answer: "It is 2 mg [1].",
  citations: [],
  refusal: null,
  model: "gpt-6.1-sol",
  usage,
  timings,
  answer_id: "a1",
  ...overrides,
});

function play(events: AnswerEvent[]): Turn {
  return events.reduce(applyEvent, startTurn("t1", "What is the dose?"));
}

describe("chat turns", () => {
  it("streams text, then takes the checked answer from done", () => {
    const turn = play([
      { kind: "sources", query: "What is the dose?", passages: [hit("2 mg")] },
      { kind: "delta", text: "It is " },
      { kind: "delta", text: "2 mg [1, 7]." },
      done(),
    ]);

    expect(turn.status).toBe("answered");
    expect(turn.sources).toHaveLength(1);
    expect(turn.text).toBe("It is 2 mg [1].");
  });

  it("is answering, with partial text, while deltas arrive", () => {
    const turn = play([
      { kind: "sources", query: "q", passages: [] },
      { kind: "delta", text: "It is" },
    ]);

    expect(turn.status).toBe("answering");
    expect(turn.text).toBe("It is");
  });

  it("clears streamed text when the answer is refused", () => {
    const refusal = {
      reason: "no_citations" as const,
      message: "No passage backs this.",
      closest: [],
    };
    const turn = play([
      { kind: "delta", text: "It is 2 mg." },
      done({ answer: "", refusal }),
    ]);

    expect(turn.status).toBe("refused");
    expect(turn.text).toBe("");
  });

  it("keeps a typed error", () => {
    const turn = play([
      { kind: "error", code: "llm_rate_limited", message: "Rate limit." },
    ]);

    expect(turn.status).toBe("failed");
    expect(turn.error).toEqual({
      code: "llm_rate_limited",
      message: "Rate limit.",
    });
  });

  it("marks a stream that broke off as stopped or failed, but not a finished one", () => {
    const streaming = play([{ kind: "sources", query: "q", passages: [] }]);

    expect(endTurn(streaming, "stopped").status).toBe("stopped");
    expect(
      endTurn(streaming, { code: "network", message: "Lost connection." })
        .status,
    ).toBe("failed");
    expect(endTurn(play([done()]), "stopped").status).toBe("answered");
  });

  it("sends only finished turns as history, the latest five", () => {
    const answered = (i: number) => ({
      ...play([done()]),
      id: `t${i}`,
      question: `q${i}`,
    });
    const turns = [
      ...Array.from({ length: 6 }, (_, i) => answered(i)),
      endTurn(startTurn("x", "broken"), { code: "network", message: "lost" }),
    ];

    const history = historyFor(turns);

    expect(history.map((t) => t.question)).toEqual([
      "q1",
      "q2",
      "q3",
      "q4",
      "q5",
    ]);
    expect(history[0]).toEqual({ question: "q1", answer: "It is 2 mg [1]." });
  });
});

describe("citation markers", () => {
  it("links markers that name a passage and leaves the rest as text", () => {
    expect(
      linkCitations("2 mg [1][3]. See [9] and [the label](https://x.test).", 3),
    ).toBe(
      "2 mg [1](#cite-1)[3](#cite-3). See [9] and [the label](https://x.test).",
    );
  });

  it("reads the passage number back from a link", () => {
    expect(citedPassage("#cite-12")).toBe(12);
    expect(citedPassage("https://x.test")).toBeNull();
    expect(citedPassage(undefined)).toBeNull();
  });
});

describe("streamAnswer", () => {
  const sse = (...events: object[]) =>
    new Response(events.map((e) => `data: ${JSON.stringify(e)}\n\n`).join(""), {
      headers: { "content-type": "text/event-stream" },
    });

  it("posts the question to the web app's API route and yields events", async () => {
    const calls: { url: string; body: string }[] = [];
    const fetchImpl = (async (url: RequestInfo | URL, init?: RequestInit) => {
      calls.push({ url: String(url), body: String(init?.body) });
      return sse(
        { kind: "sources", query: "q", passages: [] },
        { kind: "future_kind" },
        done(),
      );
    }) as typeof fetch;

    const kinds: string[] = [];
    for await (const event of streamAnswer(
      { question: "q", history: [] },
      { fetchImpl },
    )) {
      kinds.push(event.kind);
    }

    expect(calls[0]).toEqual({
      url: "/api/answers",
      body: '{"question":"q","history":[]}',
    });
    expect(kinds).toEqual(["sources", "done"]); // the unknown kind is skipped
  });

  it("turns an API error response into a typed error", async () => {
    const fetchImpl = (async () =>
      Response.json(
        {
          code: "unknown_documents",
          message: "No document with id x.",
          details: [],
        },
        { status: 404 },
      )) as typeof fetch;

    const events = streamAnswer({ question: "q" }, { fetchImpl });

    await expect(events.next()).rejects.toEqual(
      new AnswerRequestError(
        404,
        "unknown_documents",
        "No document with id x.",
      ),
    );
  });

  it("ignores data that is not an event", () => {
    expect(parseEvent("[1, 2]")).toBeNull();
    expect(parseEvent('{"kind":"delta","text":"x"}')).toEqual({
      kind: "delta",
      text: "x",
    });
  });
});
