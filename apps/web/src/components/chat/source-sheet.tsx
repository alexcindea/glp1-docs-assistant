"use client";

import { useEffect, useRef, useSyncExternalStore, type ReactNode } from "react";

/**
 * The source panel as a full-screen dialog on narrow screens. Focus moves
 * into it on open and back to `opener` (the citation clicked) on close;
 * Escape closes it. The page behind is made inert by the caller, so Tab
 * can't reach controls hidden under the sheet.
 */
export function SourceSheet({
  onClose,
  opener,
  children,
}: {
  onClose: () => void;
  // Noted when it was clicked: by the time the sheet mounts, making the page
  // behind inert has already taken focus away from it.
  opener: HTMLElement | null;
  children: ReactNode;
}) {
  const sheet = useRef<HTMLDivElement>(null);
  useEffect(() => {
    sheet.current?.focus();
    return () => opener?.focus();
  }, [opener]);
  return (
    <div
      ref={sheet}
      tabIndex={-1}
      role="dialog"
      aria-modal="true"
      aria-label="Sources"
      onKeyDown={(event) => {
        if (event.key === "Escape") onClose();
      }}
      className="bg-background fixed inset-0 z-40 flex flex-col outline-none"
    >
      {children}
    </div>
  );
}

/** Whether the media query matches, kept current; false while rendering on the server. */
export function useMediaQuery(query: string): boolean {
  return useSyncExternalStore(
    (onChange) => {
      const list = window.matchMedia(query);
      list.addEventListener("change", onChange);
      return () => list.removeEventListener("change", onChange);
    },
    () => window.matchMedia(query).matches,
    () => false,
  );
}
