import { describe, expect, it } from "vitest";

import {
  anyWorking,
  documentName,
  documentState,
  pageLabel,
  type DocumentOut,
} from "./documents";
import { uploadResult } from "./upload";

const doc = (overrides: Partial<DocumentOut> = {}): DocumentOut => ({
  id: "d1",
  filename: "fda-ozempic-label-2026.pdf",
  size_bytes: 1000,
  page_count: 72,
  scanned_pages: 0,
  status: "ready",
  error_code: null,
  error: null,
  parser: "docling",
  created_at: "2026-10-09T00:00:00Z",
  updated_at: "2026-10-09T00:00:00Z",
  active_job: null,
  ...overrides,
});

describe("documentState", () => {
  it("shows reading progress from the running job", () => {
    const state = documentState(
      doc({
        status: "processing",
        active_job: {
          kind: "ingest",
          status: "running",
          pages_done: 18,
          pages_total: 72,
        },
      }),
    );

    expect(state).toEqual({
      label: "Reading pages",
      tone: "working",
      progress: 0.25,
      detail: "18 of 72 pages",
    });
  });

  it("waits while the job is queued", () => {
    const queued = doc({
      status: "queued",
      active_job: {
        kind: "ingest",
        status: "queued",
        pages_done: 0,
        pages_total: 72,
      },
    });

    expect(documentState(queued).label).toBe("Waiting for a worker");
    expect(anyWorking([doc(), queued])).toBe(true);
    expect(anyWorking([doc()])).toBe(false);
  });

  it("shows why a document failed", () => {
    const failed = doc({
      status: "failed",
      error: "The PDF could not be read.",
    });

    expect(documentState(failed)).toMatchObject({
      tone: "failed",
      detail: "The PDF could not be read.",
    });
  });
});

describe("labels", () => {
  it("names documents without .pdf and labels page ranges", () => {
    expect(documentName("label.PDF")).toBe("label");
    expect(pageLabel(4, 4)).toBe("p. 4");
    expect(pageLabel(4, 5)).toBe("pp. 4–5");
  });
});

describe("uploadResult", () => {
  it("tells a new document from one uploaded before", () => {
    const body = JSON.stringify(doc({ status: "queued" }));

    expect(uploadResult(202, body).kind).toBe("queued");
    expect(uploadResult(200, body).kind).toBe("existing");
  });

  it("passes on the API's typed refusal", () => {
    const body = JSON.stringify({
      code: "not_a_pdf",
      message: "This file is not a PDF.",
      details: [],
    });

    expect(uploadResult(415, body)).toEqual({
      kind: "rejected",
      code: "not_a_pdf",
      message: "This file is not a PDF.",
    });
  });

  it("explains a response that isn't from the API", () => {
    expect(uploadResult(502, "<html>Bad gateway</html>")).toMatchObject({
      kind: "rejected",
      code: "http_error",
    });
  });
});
