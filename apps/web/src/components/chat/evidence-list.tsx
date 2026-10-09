"use client";

import { Badge } from "@/components/ui/badge";
import type { Schemas } from "@/lib/api/client";
import { documentName, pageLabel } from "@/lib/documents";
import { passagePages } from "@/lib/highlights";
import { cn } from "@/lib/utils";

type Props = {
  query: string | null;
  sources: Schemas["SearchHit"][];
  cited: Set<number>;
  selected: number | null;
  onOpen: (n: number) => void;
};

/**
 * Every passage the model was given, in the order retrieval ranked them,
 * with what each search path made of it: the answer's evidence, including
 * what it chose not to cite.
 */
export function EvidenceList({
  query,
  sources,
  cited,
  selected,
  onOpen,
}: Props) {
  return (
    <div className="min-h-0 flex-1 overflow-y-auto">
      <div className="text-muted-foreground space-y-1 border-b px-4 py-2.5 text-xs">
        {query && <p>Searched for: {query}</p>}
        <p>
          {sources.length} passages given to the model, {cited.size} cited.
          Ranked by combining meaning search and keyword search.
        </p>
      </div>
      <ol className="divide-y">
        {sources.map((hit, i) => {
          const n = i + 1;
          const pages = passagePages(hit.chunk.spans);
          return (
            <li key={hit.chunk.id}>
              <button
                type="button"
                onClick={() => onOpen(n)}
                className={cn(
                  "hover:bg-muted/60 w-full space-y-1 px-4 py-2.5 text-left transition-colors",
                  selected === n && "bg-muted",
                )}
                aria-label={`Open source ${n}: ${documentName(hit.filename)}`}
              >
                <div className="flex items-center gap-2">
                  <span className="inline-flex h-5 min-w-5 items-center justify-center rounded-md border font-mono text-[0.7rem] font-medium tabular-nums">
                    {n}
                  </span>
                  <span className="truncate text-sm font-medium">
                    {documentName(hit.filename)}
                  </span>
                  <span className="text-muted-foreground shrink-0 text-xs">
                    {pageLabel(pages[0] ?? 1, pages.at(-1) ?? 1)}
                  </span>
                  {cited.has(n) && (
                    <Badge className="ml-auto shrink-0">Cited</Badge>
                  )}
                </div>
                {hit.chunk.section_path.length > 0 && (
                  <p className="text-muted-foreground truncate text-xs">
                    {hit.chunk.section_path.join(" › ")}
                  </p>
                )}
                <p className="line-clamp-2 text-xs leading-relaxed">
                  {hit.chunk.text}
                </p>
                <p className="text-muted-foreground text-[0.7rem]">
                  {whyFound(hit.ranks)}
                </p>
              </button>
            </li>
          );
        })}
      </ol>
    </div>
  );
}

/** "Meaning #2 · keywords #5", or which single path found it. */
export function whyFound(ranks: Schemas["PathRanks"]): string {
  const parts = [
    ranks.vector !== null ? `meaning search #${ranks.vector}` : null,
    ranks.bm25 !== null ? `keyword search #${ranks.bm25}` : null,
  ].filter(Boolean);
  if (parts.length === 2) return `Found by ${parts.join(" and ")}`;
  return `Found only by ${parts[0] ?? "neither search"}`;
}
