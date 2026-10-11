"use client";

import "react-pdf/dist/Page/TextLayer.css";

import { ChevronLeft, ChevronRight, ZoomIn, ZoomOut } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Document, Page, pdfjs } from "react-pdf";

import { Button } from "@/components/ui/button";
import { Skeleton } from "@/components/ui/skeleton";
import { highlightBoxes, passagePages, type Span } from "@/lib/highlights";
import { cn } from "@/lib/utils";

// Must be set in the module that renders <Document> (react-pdf's own advice):
// elsewhere, module order can reset it. pdfjs-dist is pinned to react-pdf's version.
pdfjs.GlobalWorkerOptions.workerSrc = new URL(
  "pdfjs-dist/build/pdf.worker.min.mjs",
  import.meta.url,
).toString();

const ZOOMS = [0.75, 1, 1.25, 1.5, 2];

// Files pdf.js fetches as needed, served from public/pdfjs (copied from
// pdfjs-dist by scripts/copy-pdfjs-assets.mjs): image decoders for scanned
// pages (JBIG2, JPEG 2000) and the standard fonts. One object for every render,
// so react-pdf doesn't reload the document.
const PDF_OPTIONS = {
  wasmUrl: "/pdfjs/wasm/",
  standardFontDataUrl: "/pdfjs/standard_fonts/",
  // PDFs are uploads, so untrusted: never compile code from one (pdf.js uses
  // eval for some fonts' speed-up only).
  isEvalSupported: false,
};

type Props = {
  documentId: string;
  spans: Span[]; // the passage to highlight
  focusKey: string; // changes on every citation click: jump to the passage again
};

/**
 * One page of a document at a time, with the passage highlighted over it.
 *
 * The page fits the panel's width (times the zoom). Highlights are boxes
 * positioned by percentage over the rendered page (lib/highlights), so they
 * stay aligned at any size. Each citation click jumps to the passage's first
 * page and scrolls its highlight into view, also when that page is already
 * shown; the time from the click to that moment is recorded as the
 * performance measure "citation-to-highlight". Remounted per document (keyed
 * by the panel), so a page drawn for one document never counts for another.
 */
export default function PdfViewer({ documentId, spans, focusKey }: Props) {
  const pages = passagePages(spans);
  const firstPage = pages[0] ?? 1;
  // The page shown belongs to the citation it was chosen for: a new citation
  // starts at its own first page (state adjusted while rendering, no effect).
  const [view, setView] = useState({ focusKey, page: firstPage });
  if (view.focusKey !== focusKey) setView({ focusKey, page: firstPage });
  const page = view.focusKey === focusKey ? view.page : firstPage;
  const goTo = (n: number) => setView({ focusKey, page: n });

  const [numPages, setNumPages] = useState<number | null>(null);
  const [zoom, setZoom] = useState(1);
  const scroller = useRef<HTMLDivElement>(null);
  const width = useWidth(scroller);
  const focused = useRef<string | null>(null);
  const boxes = highlightBoxes(spans, page);

  // The page last drawn. Focusing waits for it to be the page shown, so the
  // highlight is on screen before it is scrolled to and timed.
  const [renderedPage, setRenderedPage] = useState<number | null>(null);
  useEffect(() => {
    if (renderedPage !== page || focused.current === focusKey) return;
    focused.current = focusKey; // once per click: after that, the user is reading
    scroller.current
      ?.querySelector("[data-highlight]")
      ?.scrollIntoView({ block: "center", inline: "nearest" });
    try {
      performance.measure("citation-to-highlight", "citation-click");
    } catch {
      // no click mark (the viewer opened some other way): nothing to measure
    }
  }, [focusKey, page, renderedPage]);

  const file = `/api/documents/${documentId}/file`;
  return (
    <div className="flex h-full min-h-0 flex-col">
      <div className="flex shrink-0 items-center gap-1 border-b px-2 py-1.5 text-xs">
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => goTo(page - 1)}
          disabled={page <= 1}
          aria-label="Previous page"
        >
          <ChevronLeft />
        </Button>
        <span className="min-w-20 text-center tabular-nums">
          Page {page}
          {numPages ? ` of ${numPages}` : ""}
        </span>
        <Button
          variant="ghost"
          size="icon-sm"
          onClick={() => goTo(page + 1)}
          disabled={numPages === null || page >= numPages}
          aria-label="Next page"
        >
          <ChevronRight />
        </Button>
        {pages.length > 1 && (
          <div
            className="ml-2 flex items-center gap-1"
            aria-label="Pages of this passage"
          >
            <span className="text-muted-foreground">Passage on</span>
            {pages.map((p) => (
              <Button
                key={p}
                variant={p === page ? "secondary" : "ghost"}
                size="xs"
                onClick={() => goTo(p)}
              >
                p. {p}
              </Button>
            ))}
          </div>
        )}
        <div className="ml-auto flex items-center">
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() =>
              setZoom(ZOOMS[Math.max(0, ZOOMS.indexOf(zoom) - 1)] ?? 1)
            }
            disabled={zoom === ZOOMS[0]}
            aria-label="Zoom out"
          >
            <ZoomOut />
          </Button>
          <span className="w-10 text-center tabular-nums">
            {Math.round(zoom * 100)}%
          </span>
          <Button
            variant="ghost"
            size="icon-sm"
            onClick={() =>
              setZoom(
                ZOOMS[Math.min(ZOOMS.length - 1, ZOOMS.indexOf(zoom) + 1)] ?? 1,
              )
            }
            disabled={zoom === ZOOMS.at(-1)}
            aria-label="Zoom in"
          >
            <ZoomIn />
          </Button>
        </div>
      </div>
      <div
        ref={scroller}
        className="bg-muted/40 min-h-0 flex-1 overflow-auto p-3"
      >
        {width > 0 && (
          <Document
            file={file}
            options={PDF_OPTIONS}
            onLoadSuccess={(pdf) => setNumPages(pdf.numPages)}
            loading={<PageSkeleton />}
            error={
              <p className="p-4 text-sm" role="alert">
                Couldn&apos;t load this PDF.{" "}
                <a
                  className="underline"
                  href={file}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open it directly
                </a>
                .
              </p>
            }
          >
            <div
              className="relative mx-auto w-fit bg-white shadow-sm"
              data-testid="pdf-page"
              data-page={page}
            >
              <Page
                pageNumber={page}
                width={Math.round((width - 24) * zoom)}
                renderAnnotationLayer={false}
                onRenderSuccess={(drawn) => setRenderedPage(drawn.pageNumber)}
                loading={<PageSkeleton />}
              />
              <div className="pointer-events-none absolute inset-0" aria-hidden>
                {boxes.map((box, i) => (
                  <div
                    key={i}
                    data-highlight
                    className={cn(
                      "bg-highlight/40 ring-highlight absolute rounded-xs mix-blend-multiply ring-1",
                      "dark:mix-blend-normal",
                    )}
                    style={{
                      left: `${box.left}%`,
                      top: `${box.top}%`,
                      width: `${box.width}%`,
                      height: `${box.height}%`,
                    }}
                  />
                ))}
              </div>
            </div>
          </Document>
        )}
      </div>
    </div>
  );
}

function PageSkeleton() {
  return <Skeleton className="mx-auto aspect-8.5/11 w-full max-w-full" />;
}

/** The element's content width, kept current as the panel resizes. */
function useWidth(ref: React.RefObject<HTMLElement | null>): number {
  const [width, setWidth] = useState(0);
  useEffect(() => {
    const element = ref.current;
    if (!element) return;
    const observer = new ResizeObserver(([entry]) => {
      if (entry) setWidth(Math.floor(entry.contentRect.width));
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);
  return width;
}
