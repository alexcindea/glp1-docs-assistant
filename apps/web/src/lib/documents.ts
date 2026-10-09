import {
  API_URL,
  createApiClient,
  type ApiClient,
  type Schemas,
} from "./api/client";

export type DocumentOut = Schemas["DocumentOut"];

export type DocumentsResult =
  | { kind: "ok"; documents: DocumentOut[] }
  | { kind: "unreachable"; message: string };

/** The document pool, newest first (server-side: the browser goes through /api). */
export async function fetchDocuments(
  client: ApiClient = createApiClient(),
  apiUrl: string = API_URL,
): Promise<DocumentsResult> {
  try {
    const { data, response } = await client.GET("/documents", {
      cache: "no-store",
    });
    if (data) return { kind: "ok", documents: data };
    return {
      kind: "unreachable",
      message: `The API answered with an unexpected response (HTTP ${response.status}).`,
    };
  } catch (error) {
    if (!(error instanceof TypeError)) throw error; // fetch's network failure; else a bug
    return {
      kind: "unreachable",
      message: `Cannot reach the API at ${apiUrl}.`,
    };
  }
}

/** How a document is named in the UI: its file name without ".pdf". */
export function documentName(filename: string): string {
  return filename.replace(/\.pdf$/i, "");
}

/** "p. 4", or "pp. 4–5" for a passage across a page break. */
export function pageLabel(first: number, last: number): string {
  return first === last ? `p. ${first}` : `pp. ${first}–${last}`;
}
