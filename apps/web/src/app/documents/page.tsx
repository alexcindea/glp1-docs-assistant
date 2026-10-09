import type { Metadata } from "next";
import { connection } from "next/server";
import { Suspense } from "react";

import { DocumentsView } from "@/components/documents/documents-view";
import { fetchDocuments } from "@/lib/documents";

export const metadata: Metadata = { title: "Documents · Docs Assistant" };

async function Documents() {
  await connection(); // always the current list, never prerendered
  const result = await fetchDocuments();
  return result.kind === "ok" ? (
    <DocumentsView initial={result.documents} error={null} />
  ) : (
    <DocumentsView initial={[]} error={result.message} />
  );
}

export default function DocumentsPage() {
  return (
    <Suspense
      fallback={<p className="text-muted-foreground p-6 text-sm">Loading…</p>}
    >
      <Documents />
    </Suspense>
  );
}
