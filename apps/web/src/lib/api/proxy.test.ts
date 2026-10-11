// @vitest-environment node
import { describe, expect, it } from "vitest";

import { proxyToApi } from "./proxy";

const API = "http://api.test:8000";

type Captured = { url: string; init: RequestInit & { duplex?: string } };

function upstream(response: Response) {
  const calls: Captured[] = [];
  const fetchImpl = async (input: RequestInfo | URL, init?: RequestInit) => {
    calls.push({ url: String(input), init: init ?? {} });
    return response;
  };
  return { calls, fetchImpl: fetchImpl as typeof fetch };
}

describe("proxyToApi", () => {
  it("forwards path, query and method, without per-connection headers", async () => {
    const { calls, fetchImpl } = upstream(Response.json([]));
    const request = new Request("http://web.test/api/documents?limit=5", {
      headers: { connection: "keep-alive", "x-request-id": "abc" },
    });

    const response = await proxyToApi(request, ["documents"], API, fetchImpl);

    expect(response.status).toBe(200);
    expect(first(calls).url).toBe("http://api.test:8000/documents?limit=5");
    expect(first(calls).init.method).toBe("GET");
    const sent = new Headers(first(calls).init.headers);
    expect(sent.get("x-request-id")).toBe("abc");
    expect(sent.has("connection")).toBe(false);
    expect(first(calls).init.body).toBeUndefined();
  });

  it("streams a request body through instead of buffering it", async () => {
    const { calls, fetchImpl } = upstream(new Response(null, { status: 200 }));
    const request = new Request("http://web.test/api/answers", {
      method: "POST",
      body: JSON.stringify({ question: "dose?" }),
      headers: { "content-type": "application/json" },
    });

    await proxyToApi(request, ["answers"], API, fetchImpl);

    expect(first(calls).init.body).toBeInstanceOf(ReadableStream);
    expect(first(calls).init.duplex).toBe("half");
  });

  it("passes event streams through untouched and keeps gzip off them", async () => {
    const events = 'data: {"kind":"delta","text":"2 mg"}\n\n';
    const { fetchImpl } = upstream(
      new Response(events, {
        headers: {
          "content-type": "text/event-stream",
          "cache-control": "no-cache",
        },
      }),
    );

    const response = await proxyToApi(
      new Request("http://web.test/api/answers", {
        method: "POST",
        body: "{}",
      }),
      ["answers"],
      API,
      fetchImpl,
    );

    expect(response.headers.get("cache-control")).toBe(
      "no-cache, no-transform",
    );
    expect(await response.text()).toBe(events);
  });

  it("keeps the PDF's length and caching headers", async () => {
    const { fetchImpl } = upstream(
      new Response(new Uint8Array([37, 80, 68, 70]), {
        headers: {
          "content-type": "application/pdf",
          "content-length": "4",
          etag: '"abc"',
        },
      }),
    );

    const response = await proxyToApi(
      new Request("http://web.test/api/documents/x/file"),
      ["documents", "x", "file"],
      API,
      fetchImpl,
    );

    expect(response.headers.get("content-length")).toBe("4");
    expect(response.headers.get("etag")).toBe('"abc"');
  });

  it("refuses paths outside the API's public routes", async () => {
    const { calls, fetchImpl } = upstream(Response.json({}));

    const response = await proxyToApi(
      new Request("http://web.test/api/openapi.json"),
      ["openapi.json"],
      API,
      fetchImpl,
    );

    expect(response.status).toBe(404);
    expect(calls).toHaveLength(0);
  });

  it("refuses dot segments that would step outside the allowed routes", async () => {
    const { calls, fetchImpl } = upstream(Response.json({}));

    const response = await proxyToApi(
      new Request("http://web.test/api/documents/../openapi.json"),
      ["documents", "..", "openapi.json"],
      API,
      fetchImpl,
    );

    expect(response.status).toBe(404);
    expect(calls).toHaveLength(0);
  });

  it("refuses changes requested by another site, but not reads or same-site posts", async () => {
    const post = (headers: Record<string, string>) =>
      new Request("http://web.test/api/documents", {
        method: "POST",
        body: "x",
        headers,
      });
    const { calls, fetchImpl } = upstream(new Response(null, { status: 202 }));

    const crossSite = await proxyToApi(
      post({ "sec-fetch-site": "cross-site" }),
      ["documents"],
      API,
      fetchImpl,
    );
    const otherOrigin = await proxyToApi(
      post({ origin: "https://evil.test", host: "web.test" }),
      ["documents"],
      API,
      fetchImpl,
    );
    const sameSite = await proxyToApi(
      post({ "sec-fetch-site": "same-origin" }),
      ["documents"],
      API,
      fetchImpl,
    );
    const crossSiteRead = await proxyToApi(
      new Request("http://web.test/api/documents", {
        headers: { "sec-fetch-site": "cross-site" },
      }),
      ["documents"],
      API,
      fetchImpl,
    );

    expect([crossSite.status, otherOrigin.status]).toEqual([403, 403]);
    expect(await crossSite.json()).toMatchObject({ code: "cross_site" });
    expect([sameSite.status, crossSiteRead.status]).toEqual([202, 202]);
    expect(calls).toHaveLength(2);
  });

  it("reports an unreachable API as a typed 502", async () => {
    const failing = (async () => {
      throw new TypeError("fetch failed");
    }) as typeof fetch;

    const response = await proxyToApi(
      new Request("http://web.test/api/health"),
      ["health"],
      API,
      failing,
    );

    expect(response.status).toBe(502);
    expect(await response.json()).toMatchObject({ code: "api_unreachable" });
  });
});

function first(calls: Captured[]): Captured {
  const [call] = calls;
  if (!call) throw new Error("the API was not called");
  return call;
}
