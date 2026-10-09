// @vitest-environment node
import { describe, expect, it } from "vitest";

import { readEventData } from "./sse";

/** A body that arrives in exactly these pieces. */
function body(...chunks: (string | Uint8Array)[]): ReadableStream<Uint8Array> {
  const encoder = new TextEncoder();
  return new ReadableStream({
    start(controller) {
      for (const chunk of chunks) {
        controller.enqueue(
          typeof chunk === "string" ? encoder.encode(chunk) : chunk,
        );
      }
      controller.close();
    },
  });
}

async function all(stream: ReadableStream<Uint8Array>): Promise<string[]> {
  const out: string[] = [];
  for await (const data of readEventData(stream)) out.push(data);
  return out;
}

describe("readEventData", () => {
  it("yields the data of each event", async () => {
    expect(await all(body('data: {"a":1}\n\ndata: {"b":2}\n\n'))).toEqual([
      '{"a":1}',
      '{"b":2}',
    ]);
  });

  it("joins events split across chunks anywhere", async () => {
    const events = await all(
      body("da", 'ta: {"kind":"de', 'lta"}', "\n", "\n"),
    );

    expect(events).toEqual(['{"kind":"delta"}']);
  });

  it("decodes a character split between chunks", async () => {
    const bytes = new TextEncoder().encode('data: "≥ 2 µg"\n\n');
    const cut = 10; // inside the 3-byte "≥"

    expect(await all(body(bytes.slice(0, cut), bytes.slice(cut)))).toEqual([
      '"≥ 2 µg"',
    ]);
  });

  it("handles CRLF line endings, even split between chunks", async () => {
    expect(
      await all(body("data: one\r", "\n\r", "\ndata: two\r\n\r\n")),
    ).toEqual(["one", "two"]);
  });

  it("skips keep-alive comments and fields it doesn't use", async () => {
    const stream = body(": ping\n\nevent: x\nid: 3\ndata: kept\n\n");

    expect(await all(stream)).toEqual(["kept"]);
  });

  it("joins multi-line data with newlines", async () => {
    expect(await all(body("data: a\ndata: b\n\n"))).toEqual(["a\nb"]);
  });

  it("delivers a last event that has no closing blank line", async () => {
    expect(await all(body("data: first\n\ndata: last"))).toEqual([
      "first",
      "last",
    ]);
  });

  it("cancels the body when the reader stops early", async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(new TextEncoder().encode("data: 1\n\ndata: 2\n\n"));
      },
      cancel() {
        cancelled = true;
      },
    });

    for await (const data of readEventData(stream)) {
      expect(data).toBe("1");
      break;
    }

    expect(cancelled).toBe(true);
  });
});
