"use client";

import {
  AlertTriangle,
  CheckCircle2,
  FileUp,
  Loader2,
  MessageSquare,
} from "lucide-react";
import Link from "next/link";
import { useEffect, useRef, useState, type DragEvent } from "react";

import { Badge } from "@/components/ui/badge";
import { buttonVariants } from "@/components/ui/button";
import {
  anyWorking,
  documentName,
  documentState,
  type DocumentOut,
} from "@/lib/documents";
import { uploadDocument, type UploadResult } from "@/lib/upload";
import { cn } from "@/lib/utils";

const POLL_MS = 2000;

type Upload = {
  key: string;
  name: string;
  sent: number; // 0-1 of the bytes
  result: UploadResult | null;
};

type Props = { initial: DocumentOut[]; error: string | null };

/**
 * The document pool: upload PDFs and watch them being read. The list refreshes
 * every two seconds while any document is still queued or being read.
 */
export function DocumentsView({ initial, error }: Props) {
  const [documents, setDocuments] = useState(initial);
  const [uploads, setUploads] = useState<Upload[]>([]);
  const [dragging, setDragging] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const working =
    anyWorking(documents) || uploads.some((u) => u.result === null);

  useEffect(() => {
    if (!working) return;
    const timer = setInterval(async () => {
      const latest = await listDocuments();
      if (latest) setDocuments(latest);
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [working]);

  async function refresh() {
    const latest = await listDocuments();
    if (latest) setDocuments(latest);
  }

  async function upload(files: FileList | File[]) {
    for (const file of Array.from(files)) {
      const key = crypto.randomUUID();
      setUploads((u) => [
        { key, name: file.name, sent: 0, result: null },
        ...u,
      ]);
      const update = (change: Partial<Upload>) =>
        setUploads((u) =>
          u.map((x) => (x.key === key ? { ...x, ...change } : x)),
        );
      const result = await uploadDocument(file, (sent) => update({ sent }));
      update({ result, sent: 1 });
      await refresh();
    }
  }

  function onDrop(event: DragEvent) {
    event.preventDefault();
    setDragging(false);
    if (event.dataTransfer.files.length) void upload(event.dataTransfer.files);
  }

  return (
    <main className="mx-auto w-full max-w-4xl space-y-6 px-4 py-8">
      <div className="space-y-1">
        <h1 className="text-xl font-semibold">Documents</h1>
        <p className="text-muted-foreground text-sm">
          Questions are answered from these PDFs. Scanned pages are read with
          OCR; a document can be asked about once it is ready.
        </p>
      </div>

      <label
        htmlFor="upload"
        onDragOver={(e) => {
          e.preventDefault();
          setDragging(true);
        }}
        onDragLeave={() => setDragging(false)}
        onDrop={onDrop}
        className={cn(
          "flex cursor-pointer flex-col items-center gap-2 rounded-xl border-2 border-dashed p-8 text-center transition-colors",
          dragging
            ? "border-primary bg-primary/5"
            : "hover:border-primary/50 hover:bg-muted/40",
        )}
      >
        <FileUp className="text-muted-foreground size-6" />
        <span className="text-sm font-medium">
          Drop PDFs here, or choose files
        </span>
        <span className="text-muted-foreground text-xs">
          The same file uploaded twice is stored once.
        </span>
        <input
          ref={input}
          id="upload"
          type="file"
          accept="application/pdf,.pdf"
          multiple
          className="sr-only"
          onChange={(e) => {
            if (e.target.files?.length) void upload(e.target.files);
            e.target.value = "";
          }}
        />
      </label>

      {uploads.length > 0 && (
        <ul className="space-y-2" aria-label="Uploads">
          {uploads.map((u) => (
            <UploadRow key={u.key} upload={u} />
          ))}
        </ul>
      )}

      {error && (
        <p
          className="border-destructive/30 bg-destructive/5 rounded-md border p-3 text-sm"
          role="alert"
        >
          {error}
        </p>
      )}

      <section className="space-y-2" aria-label="Document list">
        <h2 className="text-sm font-medium">
          {documents.length} document{documents.length === 1 ? "" : "s"}
        </h2>
        <ul className="divide-y rounded-lg border">
          {documents.map((d) => (
            <DocumentRow key={d.id} document={d} />
          ))}
          {documents.length === 0 && (
            <li className="text-muted-foreground p-4 text-sm">
              No documents yet.
            </li>
          )}
        </ul>
      </section>
    </main>
  );
}

function UploadRow({ upload }: { upload: Upload }) {
  const { result } = upload;
  return (
    <li className="flex items-center gap-3 rounded-lg border px-3 py-2 text-sm">
      {result === null ? (
        <Loader2 className="text-muted-foreground size-4 shrink-0 animate-spin" />
      ) : result.kind === "rejected" ? (
        <AlertTriangle className="text-destructive size-4 shrink-0" />
      ) : (
        <CheckCircle2 className="size-4 shrink-0 text-emerald-600" />
      )}
      <span className="min-w-0 flex-1 truncate">{upload.name}</span>
      <span
        className={cn(
          "shrink-0 text-xs",
          result?.kind === "rejected"
            ? "text-destructive"
            : "text-muted-foreground",
        )}
        role={result?.kind === "rejected" ? "alert" : undefined}
      >
        {result === null
          ? `Uploading ${Math.round(upload.sent * 100)}%`
          : result.kind === "rejected"
            ? result.message
            : result.kind === "existing"
              ? "Already in the collection"
              : "Uploaded, queued for reading"}
      </span>
    </li>
  );
}

function DocumentRow({ document }: { document: DocumentOut }) {
  const state = documentState(document);
  const sizeMb = (document.size_bytes / 1_000_000).toFixed(1);
  return (
    <li
      className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3"
      data-status={document.status}
    >
      <div className="min-w-0 flex-1 basis-64">
        <p className="truncate text-sm font-medium">
          {documentName(document.filename)}
        </p>
        <p className="text-muted-foreground text-xs">
          {document.page_count} pages
          {document.scanned_pages
            ? `, ${document.scanned_pages} scanned`
            : ""}{" "}
          · {sizeMb} MB
        </p>
        {state.detail && state.tone === "failed" && (
          <p className="text-destructive text-xs">{state.detail}</p>
        )}
      </div>
      <div className="flex w-56 shrink-0 flex-col gap-1">
        <div className="flex items-center gap-2">
          <Badge
            variant={
              state.tone === "failed"
                ? "destructive"
                : state.tone === "ready"
                  ? "secondary"
                  : "outline"
            }
          >
            {state.tone === "working" && (
              <Loader2 className="animate-spin" data-icon="inline-start" />
            )}
            {state.label}
          </Badge>
          {state.detail && state.tone === "working" && (
            <span className="text-muted-foreground text-xs tabular-nums">
              {state.detail}
            </span>
          )}
        </div>
        {state.progress !== null && (
          <div
            className="bg-muted h-1.5 overflow-hidden rounded-full"
            role="progressbar"
            aria-label={`${documentName(document.filename)}: ${state.detail}`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(state.progress * 100)}
          >
            <div
              className="bg-primary h-full transition-[width]"
              style={{ width: `${state.progress * 100}%` }}
            />
          </div>
        )}
      </div>
      {document.status === "ready" && (
        <Link
          href={`/?document=${document.id}`}
          className={buttonVariants({ variant: "ghost", size: "sm" })}
          aria-label={`Ask about ${documentName(document.filename)}`}
        >
          <MessageSquare data-icon="inline-start" /> Ask
        </Link>
      )}
    </li>
  );
}

/** The current list, or null when the API can't be reached (the next poll retries). */
async function listDocuments(): Promise<DocumentOut[] | null> {
  try {
    const response = await fetch("/api/documents", { cache: "no-store" });
    return response.ok ? ((await response.json()) as DocumentOut[]) : null;
  } catch {
    return null;
  }
}
