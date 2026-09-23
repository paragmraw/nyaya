// Error humanization: maps the unified error contract's machine codes
// ({message, detail, rid}) to copy a user can act on, keeping server-provided
// detail where it adds value. Verbatim move from the old lib/chat.ts.
// Exported for unit testing.

export function humanizeError(code: string, detail = ""): string {
  const c = code.trim();
  const d = detail.trim();
  const withDetail = (base: string) => (d ? `${base} (${d})` : base);

  // Transport classes: a bare `TypeError: Failed to fetch`, a browser-specific
  // variant, or an explicit abort (user stop / stream timeout).
  if (/^(cancelled|aborted|aborterror)$/i.test(c)) return "Response cancelled.";
  // Set by the session-restoration path for a run that was mid-stream when
  // the page was refreshed (see deserializeMessages).
  if (/^interrupted$/i.test(c)) {
    return withDetail("This response was interrupted. Retry to resend your question.");
  }
  if (/failed to fetch|networkerror|load failed|network request failed/i.test(c)) {
    return withDetail("Couldn't reach the Nyaya service. Check your connection and try again.");
  }
  if (/^no response body$|^empty response$/i.test(c)) {
    return "Nyaya returned an empty response.";
  }

  // HTTP status lines from the non-2xx handler ("503 Service Unavailable").
  if (/^429\b/.test(c)) {
    return withDetail("Nyaya is handling a lot of requests right now. Try again in a moment.");
  }
  if (/^5\d\d\b/.test(c)) {
    return withDetail("Nyaya's assistant is temporarily unavailable. Please retry in a moment.");
  }
  if (/^4\d\d\b/.test(c)) {
    return withDetail("Nyaya couldn't process this request.");
  }
  if (/rate_limit/.test(c)) {
    return withDetail("Nyaya is handling a lot of requests right now. Try again in a moment.");
  }

  // Anything else that already reads like a sentence passes through (server
  // error messages may be human-phrased), with detail appended. This check
  // precedes the machine-code matches below so phrased messages ("The
  // verification layer timed out") are not re-mapped.
  const looksHuman = c.includes(" ") && /[a-z]{3}/.test(c) && !/^[a-z0-9_]+$/.test(c);
  if (looksHuman) return withDetail(c);

  // Machine codes the backend sends in `message`.
  if (/timed?\s?out|timeout/i.test(c)) {
    return withDetail("Nyaya took too long to respond. Try resending your question.");
  }
  if (/agent_unavailable|agent_error|internal_error|degraded/i.test(c)) {
    return withDetail("Nyaya's assistant is temporarily unavailable. Please retry in a moment.");
  }

  // Unknown opaque code: generic copy, with the raw code (or the detail) so
  // the user can still report it.
  return `Something went wrong while getting an answer. (${d || c})`;
}