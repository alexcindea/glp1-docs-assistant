import { connection } from "next/server";
import { Suspense } from "react";

import { ChatApp } from "@/components/chat/chat-app";
import { fetchDocuments } from "@/lib/documents";

async function Chat({
  searchParams,
}: {
  searchParams: PageProps<"/">["searchParams"];
}) {
  // The document list is read on every request, never prerendered.
  await connection();
  const [result, params] = await Promise.all([fetchDocuments(), searchParams]);
  // "Ask" on the documents page links here with ?document=<id>.
  const preselected =
    typeof params.document === "string" ? [params.document] : [];
  return result.kind === "ok" ? (
    <ChatApp
      documents={result.documents}
      documentsError={null}
      initialFilter={preselected.filter((id) =>
        result.documents.some((d) => d.id === id),
      )}
    />
  ) : (
    <ChatApp
      documents={[]}
      documentsError={result.message}
      initialFilter={[]}
    />
  );
}

export default function Home({ searchParams }: PageProps<"/">) {
  return (
    <Suspense
      fallback={<p className="text-muted-foreground p-6 text-sm">Loading…</p>}
    >
      <Chat searchParams={searchParams} />
    </Suspense>
  );
}
