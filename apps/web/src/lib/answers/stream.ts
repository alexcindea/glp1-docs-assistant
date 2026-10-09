import type { Schemas } from "@/lib/api/client";
import { readEventData } from "@/lib/sse";

/** One event of POST /answers. The types are generated from the API's schema. */
export type AnswerEvent =
  | Schemas["SourcesEvent"]
  | Schemas["DeltaEvent"]
  | Schemas["DoneEvent"]
  | Schemas["ErrorEvent"];
export type AnswerRequest = Schemas["AnswerRequest"];

const KINDS = new Set<string>(["sources", "delta", "done", "error"]);

/** The API refused the request before streaming (unknown document, invalid question). */
export class AnswerRequestError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
    this.name = "AnswerRequestError";
  }
}

/**
 * Ask a question; yields the answer's events as they arrive.
 *
 * Goes through the web app's own `/api` route, which forwards to the API.
 * Aborting `signal` stops the answer, model call included.
 */
export async function* streamAnswer(
  request: AnswerRequest,
  {
    signal,
    fetchImpl = fetch,
  }: { signal?: AbortSignal; fetchImpl?: typeof fetch } = {},
): AsyncGenerator<AnswerEvent> {
  const response = await fetchImpl("/api/answers", {
    method: "POST",
    headers: {
      "content-type": "application/json",
      accept: "text/event-stream",
    },
    body: JSON.stringify(request),
    signal,
  });
  if (!response.ok || !response.body) throw await requestError(response);
  for await (const data of readEventData(response.body)) {
    const event = parseEvent(data);
    if (event) yield event;
  }
}

/** An event, or null for a kind this version doesn't know (newer API: skip it). */
export function parseEvent(data: string): AnswerEvent | null {
  const value: unknown = JSON.parse(data);
  if (typeof value !== "object" || value === null || !("kind" in value))
    return null;
  return typeof value.kind === "string" && KINDS.has(value.kind)
    ? (value as AnswerEvent)
    : null;
}

async function requestError(response: Response): Promise<AnswerRequestError> {
  try {
    const body = (await response.json()) as Partial<Schemas["ErrorResponse"]>;
    if (typeof body.code === "string" && typeof body.message === "string") {
      const details = body.details?.length
        ? ` (${body.details.join("; ")})`
        : "";
      return new AnswerRequestError(
        response.status,
        body.code,
        body.message + details,
      );
    }
  } catch {
    // not JSON: fall through to a generic message
  }
  return new AnswerRequestError(
    response.status,
    "http_error",
    `The server answered ${response.status}. Try again in a moment.`,
  );
}
