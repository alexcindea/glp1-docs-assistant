"use client";

import { ChevronDown, Files } from "lucide-react";

import { Button } from "@/components/ui/button";
import { Checkbox } from "@/components/ui/checkbox";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { documentName, type DocumentOut } from "@/lib/documents";

type Props = {
  documents: DocumentOut[];
  selected: string[]; // empty: all documents
  onChange: (ids: string[]) => void;
};

/** Which documents the next question searches: all of them, or the ones ticked. */
export function DocumentFilter({ documents, selected, onChange }: Props) {
  const ready = documents.filter((d) => d.status === "ready");
  const label =
    selected.length === 0
      ? "All documents"
      : selected.length === 1
        ? documentName(
            ready.find((d) => d.id === selected[0])?.filename ?? "1 document",
          )
        : `${selected.length} documents`;

  function toggle(id: string, on: boolean) {
    onChange(on ? [...selected, id] : selected.filter((s) => s !== id));
  }

  return (
    <Popover>
      <PopoverTrigger
        render={
          <Button
            variant="ghost"
            size="sm"
            className="text-muted-foreground max-w-64"
          >
            <Files data-icon="inline-start" />
            <span className="truncate">{label}</span>
            <ChevronDown data-icon="inline-end" />
          </Button>
        }
      />
      <PopoverContent align="start" className="w-80 p-0">
        <div className="flex items-center justify-between border-b px-3 py-2">
          <p className="text-sm font-medium">Search in</p>
          <Button
            variant="link"
            size="xs"
            disabled={!selected.length}
            onClick={() => onChange([])}
          >
            All documents
          </Button>
        </div>
        {ready.length === 0 ? (
          <p className="text-muted-foreground p-3 text-sm">
            No documents are ready yet.
          </p>
        ) : (
          <ul className="max-h-72 overflow-y-auto p-1">
            {ready.map((d) => (
              <li key={d.id}>
                <label className="hover:bg-muted flex cursor-pointer items-center gap-2 rounded-md px-2 py-1.5 text-sm">
                  <Checkbox
                    checked={selected.includes(d.id)}
                    onCheckedChange={(on) => toggle(d.id, on)}
                  />
                  <span className="truncate">{documentName(d.filename)}</span>
                  <span className="text-muted-foreground ml-auto shrink-0 text-xs">
                    {d.page_count} p.
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </PopoverContent>
    </Popover>
  );
}
