/**
 * Server-sent events from a fetch response body.
 *
 * `EventSource` can only GET, and asking a question is a POST, so the stream is
 * read by hand. Per the SSE spec: events end at a blank line; `data:` lines
 * join with "\n"; lines starting with ":" are comments (the API's keep-alive
 * pings); `event:`, `id:` and `retry:` are not used by our API and are skipped.
 * Network chunks can split anywhere, even inside a UTF-8 character or between
 * the two characters of a CRLF, so text is decoded as a stream and a line is
 * only read once its ending has fully arrived.
 */
export async function* readEventData(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<string> {
  const reader = body.getReader();
  const decoder = new TextDecoder(); // stream mode keeps a split character for the next chunk
  let buffer = "";
  let data: string[] = [];
  try {
    for (;;) {
      const { done, value } = await reader.read();
      if (done) {
        buffer += decoder.decode();
        break;
      }
      buffer += decoder.decode(value, { stream: true });
      for (;;) {
        const line = takeLine(buffer);
        if (line === null) break;
        buffer = buffer.slice(line.consumed);
        if (line.text === "") {
          if (data.length) yield data.join("\n");
          data = [];
        } else if (line.text.startsWith("data:")) {
          data.push(dataValue(line.text));
        }
      }
    }
    // A stream that ends without the final blank line still delivers its event.
    const last = buffer.replace(/\r$/, "");
    if (last.startsWith("data:")) data.push(dataValue(last));
    if (data.length) yield data.join("\n");
  } finally {
    // Stopping early (the user pressed stop) cancels the download too.
    await reader.cancel().catch(() => undefined);
  }
}

/** The next complete line, or null if its ending hasn't fully arrived. */
function takeLine(buffer: string): { text: string; consumed: number } | null {
  const end = buffer.search(/[\r\n]/);
  if (end === -1) return null;
  if (buffer[end] === "\r") {
    if (end === buffer.length - 1) return null; // could be the first half of "\r\n"
    const consumed = buffer[end + 1] === "\n" ? end + 2 : end + 1;
    return { text: buffer.slice(0, end), consumed };
  }
  return { text: buffer.slice(0, end), consumed: end + 1 };
}

function dataValue(line: string): string {
  return line.slice(line.startsWith("data: ") ? 6 : 5);
}
