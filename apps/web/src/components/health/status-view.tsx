import { CircleCheck, CircleX } from "lucide-react";
import type { ReactNode } from "react";

import { Badge } from "@/components/ui/badge";
import {
  Card,
  CardAction,
  CardContent,
  CardDescription,
  CardHeader,
  CardTitle,
} from "@/components/ui/card";
import type { HealthResult, ReadinessReport } from "@/lib/health";

type Check = { label: string; value: string; ok: boolean };

export function healthChecks(report: ReadinessReport): Check[] {
  const db = report.database;
  const expected = db.expected_revision ?? "none";
  return [
    { label: "API", value: "Responding", ok: true },
    {
      label: "Database",
      value: db.reachable
        ? `Postgres ${db.server_version ?? "unknown version"}`
        : "Unreachable",
      ok: db.reachable,
    },
    ...db.extensions.map((ext) => ({
      label: `Extension: ${ext.name}`,
      value: ext.installed_version ?? "Not installed",
      ok: ext.installed_version !== null,
    })),
    {
      label: "Schema revision",
      value: db.reachable
        ? `${db.migration_revision ?? "none"} (expected ${expected})`
        : `Unknown (expected ${expected})`,
      ok: db.reachable && db.migration_revision === db.expected_revision,
    },
    { label: "LLM provider", value: report.models.llm_provider, ok: true },
    {
      label: "Embedder",
      value: `${report.models.embedder}, ${report.models.embedding_dimensions} dimensions`,
      ok: report.models.problems.length === 0,
    },
  ];
}

/** Render `backticked` spans of a message as inline code. */
function withInlineCode(text: string): ReactNode[] {
  return text.split("`").map((part, i) =>
    i % 2 === 1 ? (
      <code key={i} className="bg-muted rounded px-1 py-0.5 font-mono text-xs">
        {part}
      </code>
    ) : (
      part
    ),
  );
}

function StatusBadge({ ok, label }: { ok: boolean; label: string }) {
  return ok ? (
    <Badge className="bg-emerald-600 text-white">{label}</Badge>
  ) : (
    <Badge variant="destructive">{label}</Badge>
  );
}

function Warnings({ warnings }: { warnings: string[] }) {
  return (
    <ul className="space-y-1 rounded-lg bg-amber-500/10 p-3 text-amber-800 dark:text-amber-300">
      {warnings.map((warning, i) => (
        <li key={i}>{withInlineCode(warning)}</li>
      ))}
    </ul>
  );
}

function Problems({ problems }: { problems: string[] }) {
  return (
    <ul className="bg-destructive/10 text-destructive space-y-1 rounded-lg p-3">
      {problems.map((problem, i) => (
        <li key={i}>{withInlineCode(problem)}</li>
      ))}
    </ul>
  );
}

export function StatusView({ result }: { result: HealthResult }) {
  if (result.kind === "unreachable") {
    return (
      <Card>
        <CardHeader>
          <CardTitle>API</CardTitle>
          <CardDescription>{result.apiUrl}</CardDescription>
          <CardAction>
            <StatusBadge ok={false} label="Unreachable" />
          </CardAction>
        </CardHeader>
        <CardContent>
          <Problems problems={[result.message]} />
        </CardContent>
      </Card>
    );
  }

  const { report } = result;
  const ready = report.status === "ready";
  const problems = [...report.database.problems, ...report.models.problems];
  return (
    <Card>
      <CardHeader>
        <CardTitle>System status</CardTitle>
        <CardDescription>
          {ready
            ? "Everything the app needs is running."
            : "Something needs attention."}
        </CardDescription>
        <CardAction>
          <StatusBadge ok={ready} label={ready ? "Ready" : "Not ready"} />
        </CardAction>
      </CardHeader>
      <CardContent className="space-y-4">
        <dl className="divide-y">
          {healthChecks(report).map((check) => (
            <div
              key={check.label}
              className="flex items-center justify-between gap-4 py-2"
            >
              <dt className="flex items-center gap-2 font-medium">
                {check.ok ? (
                  <CircleCheck
                    aria-label="ok"
                    className="size-4 text-emerald-600"
                  />
                ) : (
                  <CircleX
                    aria-label="failing"
                    className="text-destructive size-4"
                  />
                )}
                {check.label}
              </dt>
              <dd className="text-muted-foreground text-right">
                {check.value}
              </dd>
            </div>
          ))}
        </dl>
        {problems.length > 0 && <Problems problems={problems} />}
        {report.models.warnings.length > 0 && (
          <Warnings warnings={report.models.warnings} />
        )}
      </CardContent>
    </Card>
  );
}
