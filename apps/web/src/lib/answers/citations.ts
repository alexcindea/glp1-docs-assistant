/**
 * `[n]` citation markers in answer text, made renderable.
 *
 * The answer is Markdown. Each marker naming a real passage becomes a link
 * `[n](#cite-n)`, which the renderer turns into a citation chip; markers
 * naming no passage stay plain text (the API drops those from the final
 * answer, but they can appear while it streams). Existing Markdown links
 * (`[text](url)`) are left alone.
 */
const MARKER = /\[(\d+)\](?!\()/g;
const CITE_HREF = /^#cite-(\d+)$/;

export function linkCitations(text: string, passageCount: number): string {
  return text.replace(MARKER, (marker, digits: string) => {
    const n = Number(digits);
    return n >= 1 && n <= passageCount ? `[${n}](#cite-${n})` : marker;
  });
}

/** The passage number a link points at, or null for any other link. */
export function citedPassage(href: string | undefined): number | null {
  const match = href ? CITE_HREF.exec(href) : null;
  return match ? Number(match[1]) : null;
}
