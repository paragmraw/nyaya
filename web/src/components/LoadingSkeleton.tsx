"use client";

// Route-level loading skeleton (shown by src/app/loading.tsx while a segment
// loads). Tokens-only styling so it tracks both themes; the shimmer animation
// is disabled under prefers-reduced-motion. Component-local <style> keeps
// globals.css untouched (a concurrent refactor owns that file).

export default function LoadingSkeleton() {
  return (
    <>
      <style dangerouslySetInnerHTML={{ __html: SKELETON_CSS }} />
      <main id="content" className="page" aria-busy="true" aria-live="polite">
        <div className="container" style={{ paddingTop: 24 }}>
          <div className="skeleton-bar" style={{ width: 140, height: 12 }} />
          <div className="skeleton-bar" style={{ width: "min(420px, 70%)", height: 30, marginTop: 14 }} />
          <div className="skeleton-bar" style={{ width: "min(560px, 85%)", height: 16, marginTop: 12 }} />
          <div
            style={{
              display: "grid",
              gridTemplateColumns: "repeat(auto-fit, minmax(200px, 1fr))",
              gap: 16,
              marginTop: 28,
            }}
          >
            {[0, 1, 2, 3].map((i) => (
              <div
                key={i}
                className="skeleton-card"
                style={{ height: 108, borderRadius: "var(--radius-lg, 12px)" }}
              />
            ))}
          </div>
          <div className="skeleton-bar" style={{ width: 220, height: 20, marginTop: 32 }} />
          <div className="skeleton-card" style={{ height: 180, marginTop: 14 }} />
          <span className="sr-only">Loading…</span>
        </div>
      </main>
    </>
  );
}

const SKELETON_CSS = `
.skeleton-bar, .skeleton-card {
  background: color-mix(in oklch, var(--fg) 8%, transparent);
  border-radius: var(--radius, 8px);
  animation: skeleton-pulse 1.4s ease-in-out infinite;
}
.skeleton-card { background: color-mix(in oklch, var(--fg) 5%, transparent); }
@keyframes skeleton-pulse { 0%, 100% { opacity: 0.55; } 50% { opacity: 1; } }
@media (prefers-reduced-motion: reduce) {
  .skeleton-bar, .skeleton-card { animation: none; opacity: 0.4; }
}
`;