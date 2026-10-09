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

export type DocumentState = {
  label: string;
  tone: "ready" | "working" | "failed";
  progress: number | null; // 0-1 while pages are being read
  detail: string | null;
};

/** What to show for a document: from its status and the job working on it, if any. */
export function documentState(document: DocumentOut): DocumentState {
  const job = document.active_job;
  if (job) {
    const verb = job.kind === "rechunk" ? "Re-chunking" : "Reading pages";
    if (job.status === "queued") {
      return {
        label: "Waiting for a worker",
        tone: "working",
        progress: null,
        detail: null,
      };
    }
    const total = job.pages_total || document.page_count;
    return {
      label: verb,
      tone: "working",
      progress: total ? Math.min(1, job.pages_done / total) : null,
      detail: total ? `${job.pages_done} of ${total} pages` : null,
    };
  }
  switch (document.status) {
    case "ready":
      return { label: "Ready", tone: "ready", progress: null, detail: null };
    case "failed":
      return {
        label: "Failed",
        tone: "failed",
        progress: null,
        detail:
          document.error ?? "Ingest failed. Upload the file again to retry.",
      };
    default:
      return {
        label: "Waiting for a worker",
        tone: "working",
        progress: null,
        detail: null,
      };
  }
}

/** Whether the list should keep refreshing: some document is still being read. */
export function anyWorking(documents: DocumentOut[]): boolean {
  return documents.some((d) => documentState(d).tone === "working");
}
