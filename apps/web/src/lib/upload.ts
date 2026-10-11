import type { Schemas } from "@/lib/api/client";

// 100 MB (the API's limit) at a slow 1 Mbit/s is under 14 minutes.
const UPLOAD_TIMEOUT_MS = 15 * 60_000;

export type UploadResult =
  | { kind: "queued"; document: Schemas["DocumentOut"] } // new: ingest queued
  | { kind: "existing"; document: Schemas["DocumentOut"] } // same bytes uploaded before
  | { kind: "rejected"; code: string; message: string };

/**
 * Upload one PDF through the web app's /api route, reporting bytes sent.
 *
 * XMLHttpRequest rather than fetch: fetch can't report upload progress, and a
 * 100 MB PDF takes a while. The API checks the file (type, size, pages,
 * encryption) and answers with a typed error when it refuses it.
 */
export function uploadDocument(
  file: File,
  onProgress: (fraction: number) => void,
): Promise<UploadResult> {
  return new Promise((resolve) => {
    const request = new XMLHttpRequest();
    request.open("POST", "/api/documents");
    request.upload.addEventListener("progress", (event) => {
      if (event.lengthComputable) onProgress(event.loaded / event.total);
    });
    request.addEventListener("load", () =>
      resolve(uploadResult(request.status, request.responseText)),
    );
    const interrupted = (code: string, message: string) => () =>
      resolve({ kind: "rejected", code, message });
    request.addEventListener(
      "error",
      interrupted(
        "network",
        "The upload was interrupted. Check the connection and try again.",
      ),
    );
    request.addEventListener(
      "abort",
      interrupted("aborted", "The upload was cancelled."),
    );
    request.addEventListener(
      "timeout",
      interrupted(
        "timeout",
        "The upload took too long. Try again on a faster connection.",
      ),
    );
    request.timeout = UPLOAD_TIMEOUT_MS;
    const form = new FormData();
    form.append("file", file);
    request.send(form);
  });
}

/** What the API's answer to an upload means. */
export function uploadResult(status: number, body: string): UploadResult {
  let parsed: unknown = null;
  try {
    parsed = JSON.parse(body);
  } catch {
    // not JSON: handled below
  }
  if ((status === 202 || status === 200) && isDocument(parsed)) {
    return { kind: status === 202 ? "queued" : "existing", document: parsed };
  }
  if (isError(parsed))
    return { kind: "rejected", code: parsed.code, message: parsed.message };
  return {
    kind: "rejected",
    code: "http_error",
    message: `The server answered ${status}. Try again in a moment.`,
  };
}

function isDocument(value: unknown): value is Schemas["DocumentOut"] {
  return (
    typeof value === "object" &&
    value !== null &&
    "id" in value &&
    "status" in value
  );
}

function isError(value: unknown): value is { code: string; message: string } {
  return (
    typeof value === "object" &&
    value !== null &&
    "code" in value &&
    typeof value.code === "string" &&
    "message" in value &&
    typeof value.message === "string"
  );
}
