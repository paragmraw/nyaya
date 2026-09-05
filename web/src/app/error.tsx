"use client";

// Route-level error boundary (rendered when a page component throws during
// render or hydration). Offers a retry via reset() and surfaces the error
// digest so a report can be tied to the exact failure. Tokens-only styling.

import Link from "next/link";
import { useEffect } from "react";

export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Next.js already reports this error through its own instrumentation;
    // this hook is the place for any future telemetry hookup.
  }, [error]);

  return (
    <main id="content" className="page">
      <div className="container">
        <section style={{ paddingBlock: "8vh 12vh", textAlign: "center" }}>
          <p className="eyebrow">Something broke</p>
          <h1>This page hit an unexpected error</h1>
          <p className="lead" style={{ marginInline: "auto" }}>
            Try loading it again. If the error repeats, it is on us — the
            reference below helps pinpoint it.
          </p>
          {error.digest && (
            <p style={{ fontFamily: "var(--font-mono, monospace)", fontSize: 12, color: "var(--muted)" }}>
              ref {error.digest}
            </p>
          )}
          <div style={{ display: "flex", gap: 10, justifyContent: "center", marginTop: 24 }}>
            <button type="button" className="btn btn-primary" onClick={reset}>
              Try again
            </button>
            <Link className="btn" href="/">Go home</Link>
          </div>
        </section>
      </div>
    </main>
  );
}