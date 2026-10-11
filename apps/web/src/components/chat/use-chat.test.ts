import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { useChat } from "./use-chat";

afterEach(() => vi.unstubAllGlobals());

const encoder = new TextEncoder();
const sse = (...events: object[]) =>
  events.map((event) => `data: ${JSON.stringify(event)}\n\n`).join("");

const done = (answer: string) => ({
  kind: "done",
  answer,
  citations: [],
  refusal: null,
  model: "fake-answer",
  usage: {
    input_tokens: 1,
    cached_input_tokens: 0,
    output_tokens: 1,
    reasoning_tokens: 0,
    cost_usd: 0,
  },
  timings: {
    rewrite_ms: null,
    retrieval_ms: 1,
    first_token_ms: 1,
    total_ms: 2,
  },
  answer_id: "a1",
});

/** fetch that answers each POST with the next scripted body; "hang" never finishes. */
function stubFetch(...bodies: (string | "hang")[]) {
  const sent: { question: string; history: unknown[] }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (_url: string, init: RequestInit) => {
      sent.push(JSON.parse(String(init.body)));
      const body = bodies.shift();
      const stream = new ReadableStream<Uint8Array>({
        start(controller) {
          if (body === "hang") {
            controller.enqueue(
              encoder.encode(
                sse({ kind: "sources", query: "q", passages: [] }),
              ),
            );
            controller.enqueue(
              encoder.encode(sse({ kind: "delta", text: "It is 2 mg [1]" })),
            );
            init.signal?.addEventListener("abort", () =>
              controller.error(new DOMException("Aborted", "AbortError")),
            );
            return;
          }
          controller.enqueue(encoder.encode(body ?? ""));
          controller.close();
        },
      });
      return new Response(stream, {
        headers: { "content-type": "text/event-stream" },
      });
    }),
  );
  return sent;
}

describe("useChat", () => {
  it("sends the earlier turns as history with a follow-up", async () => {
    const sent = stubFetch(
      sse(
        { kind: "sources", query: "q1", passages: [] },
        done("Ozempic: 5 days [1]."),
      ),
      sse(
        { kind: "sources", query: "q2", passages: [] },
        done("Mounjaro: 4 days [1]."),
      ),
    );
    const { result } = renderHook(() => useChat());

    await act(() =>
      result.current.ask("How long do I have after a missed Ozempic dose?", []),
    );
    await act(() => result.current.ask("And for Mounjaro?", []));

    expect(sent[0]?.history).toEqual([]);
    expect(sent[1]?.history).toEqual([
      {
        question: "How long do I have after a missed Ozempic dose?",
        answer: "Ozempic: 5 days [1].",
      },
    ]);
    expect(result.current.turns.map((t) => t.status)).toEqual([
      "answered",
      "answered",
    ]);
  });

  it("stops an answer, keeping none of its unchecked text", async () => {
    stubFetch("hang");
    const { result } = renderHook(() => useChat());

    let asking: Promise<void> | undefined;
    act(() => {
      asking = result.current.ask("What is the dose?", []);
    });
    await waitFor(() =>
      expect(result.current.turns[0]?.text).toBe("It is 2 mg [1]"),
    );
    expect(result.current.busy).toBe(true);

    await act(async () => {
      result.current.stop();
      await asking;
    });

    expect(result.current.turns[0]?.status).toBe("stopped");
    expect(result.current.turns[0]?.text).toBe("");
    expect(result.current.busy).toBe(false);
  });
});

describe("useChat, back to back", () => {
  it("takes a question asked right after an answer ended, while its stream still closes", async () => {
    const encoderLocal = new TextEncoder();
    const sent: string[] = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: RequestInit) => {
        const { question } = JSON.parse(String(init.body)) as {
          question: string;
        };
        sent.push(question);
        const stream = new ReadableStream<Uint8Array>({
          start(controller) {
            controller.enqueue(
              encoderLocal.encode(
                `data: ${JSON.stringify({ kind: "sources", query: question, passages: [] })}\n\n` +
                  `data: ${JSON.stringify(done(`${question} [1]`))}\n\n`,
              ),
            );
            // The first stream stays open after its final event; the second closes.
            if (sent.length > 1) controller.close();
            init.signal?.addEventListener("abort", () =>
              controller.error(new Error("aborted")),
            );
          },
        });
        return new Response(stream, {
          headers: { "content-type": "text/event-stream" },
        });
      }),
    );
    const { result } = renderHook(() => useChat());

    act(() => {
      void result.current.ask("first", []);
    });
    await waitFor(() =>
      expect(result.current.turns[0]?.status).toBe("answered"),
    );
    await act(() => result.current.ask("second", []));

    expect(sent).toEqual(["first", "second"]);
    expect(result.current.turns.map((t) => t.status)).toEqual([
      "answered",
      "answered",
    ]);
  });
});
