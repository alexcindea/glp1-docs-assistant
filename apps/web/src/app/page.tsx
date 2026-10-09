import Link from "next/link";
import { connection } from "next/server";
import { Suspense } from "react";

import { ChatApp } from "@/components/chat/chat-app";
import { fetchDocuments } from "@/lib/documents";

async function Chat() {
  // The document list is read on every request, never prerendered.
  await connection();
  const result = await fetchDocuments();
  return result.kind === "ok" ? (
    <ChatApp documents={result.documents} documentsError={null} />
  ) : (
    <ChatApp documents={[]} documentsError={result.message} />
  );
}

export default function Home() {
  return (
    <div className="flex h-dvh flex-col">
      <header className="flex h-12 shrink-0 items-center justify-between border-b px-4">
        <Link href="/" className="text-sm font-semibold">
          Docs Assistant
        </Link>
        <nav className="text-muted-foreground flex gap-4 text-sm">
          <Link href="/health" className="hover:text-foreground">
            Health
          </Link>
        </nav>
      </header>
      <Suspense
        fallback={<p className="text-muted-foreground p-6 text-sm">Loading…</p>}
      >
        <Chat />
      </Suspense>
    </div>
  );
}
