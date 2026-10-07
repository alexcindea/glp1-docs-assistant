import type { Metadata } from "next";
import { connection } from "next/server";
import { Suspense } from "react";

import { StatusView } from "@/components/health/status-view";
import { fetchHealth } from "@/lib/health";

export const metadata: Metadata = { title: "Health · Docs Assistant" };

async function LiveStatus() {
  // Checked on every request, never prerendered or cached.
  await connection();
  const result = await fetchHealth();
  return <StatusView result={result} />;
}

export default function HealthPage() {
  return (
    <main className="mx-auto w-full max-w-xl space-y-6 p-8">
      <h1 className="text-2xl font-semibold">Health</h1>
      <Suspense
        fallback={<p className="text-muted-foreground">Checking the API…</p>}
      >
        <LiveStatus />
      </Suspense>
    </main>
  );
}
