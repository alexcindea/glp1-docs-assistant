import { describe, expect, it } from "vitest";

import { createApiClient } from "./api/client";
import { fetchHealth, type ReadinessReport } from "./health";

const API = "http://api.test";

const report: ReadinessReport = {
  status: "not_ready",
  database: {
    reachable: false,
    expected_revision: "0001",
    extensions: [],
    problems: ["Cannot reach Postgres."],
  },
  models: {
    llm_provider: "fake",
    embedder: "fake",
    embedding_dimensions: 1024,
    problems: [],
    warnings: [],
  },
};

function clientReturning(response: () => Promise<Response>) {
  return createApiClient(API, () => response());
}

const json = (body: unknown, status: number) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });

describe("fetchHealth", () => {
  it("returns the report when the API is ready", async () => {
    const ready = { ...report, status: "ready" as const };

    const result = await fetchHealth(
      clientReturning(async () => json(ready, 200)),
      API,
    );

    expect(result).toEqual({ kind: "report", httpStatus: 200, report: ready });
  });

  it("keeps the report from a 503, since it says what is wrong", async () => {
    const result = await fetchHealth(
      clientReturning(async () => json(report, 503)),
      API,
    );

    expect(result).toEqual({ kind: "report", httpStatus: 503, report });
  });

  it("reports the API as unreachable when the request fails", async () => {
    const result = await fetchHealth(
      clientReturning(async () => {
        throw new TypeError("fetch failed");
      }),
      API,
    );

    expect(result.kind).toBe("unreachable");
    expect(result.kind === "unreachable" && result.message).toContain(API);
  });

  it("treats a body that is not a report as unreachable", async () => {
    const result = await fetchHealth(
      clientReturning(async () =>
        json({ code: "internal_error", message: "x", details: [] }, 500),
      ),
      API,
    );

    expect(result.kind).toBe("unreachable");
  });
});
