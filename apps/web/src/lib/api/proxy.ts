import { API_URL } from "./client";

/** The API paths the browser may reach; everything else is a 404 here. */
const ALLOWED_ROOTS = new Set(["answers", "documents", "health", "search"]);

/** Headers that describe one connection, not the message: never forwarded. */
const HOP_BY_HOP = [
  "connection",
  "keep-alive",
  "proxy-connection",
  "transfer-encoding",
  "upgrade",
  "te",
  "trailer",
  "host",
];

/**
 * Forward a request to the API and stream its response back unchanged.
 *
 * - Bodies stream in both directions: a 100 MB upload is not held in memory,
 *   and answer events reach the browser as the API sends them.
 * - The browser leaving aborts the upstream request, which cancels the
 *   answer (and its model call) in the API.
 * - Event streams get `no-transform`, so the server's gzip doesn't hold them
 *   back to compress them in bulk.
 */
export async function proxyToApi(
  request: Request,
  path: string[],
  apiUrl: string = API_URL,
  fetchImpl: typeof fetch = fetch,
): Promise<Response> {
  const [root] = path;
  if (root === undefined || !ALLOWED_ROOTS.has(root)) {
    return Response.json(
      { code: "not_found", message: "No such API route.", details: [] },
      { status: 404 },
    );
  }
  const target = new URL(
    path.map(encodeURIComponent).join("/"),
    apiUrl.endsWith("/") ? apiUrl : `${apiUrl}/`,
  );
  target.search = new URL(request.url).search;

  const headers = new Headers(request.headers);
  for (const name of HOP_BY_HOP) headers.delete(name);
  const hasBody = request.method !== "GET" && request.method !== "HEAD";
  // `duplex` is required to stream a request body, but not yet in the DOM types.
  const init: RequestInit & { duplex?: "half" } = {
    method: request.method,
    headers,
    body: hasBody ? request.body : undefined,
    duplex: hasBody ? "half" : undefined,
    signal: request.signal,
    redirect: "manual",
    cache: "no-store",
  };

  let upstream: Response;
  try {
    upstream = await fetchImpl(target, init);
  } catch (error) {
    if (request.signal.aborted) throw error; // the browser left; nothing to answer
    return Response.json(
      {
        code: "api_unreachable",
        message: "The API is not reachable. Is it running?",
        details: [],
      },
      { status: 502 },
    );
  }

  const responseHeaders = new Headers(upstream.headers);
  if (responseHeaders.has("content-encoding")) {
    // fetch has already decoded the body, so these no longer describe it.
    responseHeaders.delete("content-encoding");
    responseHeaders.delete("content-length");
  }
  for (const name of HOP_BY_HOP) responseHeaders.delete(name);
  if (responseHeaders.get("content-type")?.startsWith("text/event-stream")) {
    responseHeaders.set("cache-control", "no-cache, no-transform");
  }
  return new Response(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: responseHeaders,
  });
}
