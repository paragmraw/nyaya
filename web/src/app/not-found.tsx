import type { Metadata } from "next";
import Link from "next/link";
import Breadcrumb from "@/components/Breadcrumb";
import { pageOpenGraph } from "@/lib/site";

export const metadata: Metadata = {
  title: "Not found",
  description: "The page you requested doesn't exist on Nyaya.",
  robots: { index: false, follow: true },
  openGraph: pageOpenGraph({
    title: "Page not found - Nyaya",
    description: "The page you requested doesn't exist on Nyaya.",
  }),
};

// Route-level 404. Rendered for any unmatched path (next dev/static export
// both serve this for unknown routes). Tokens-only, matching the site's page
// structure so it reads as part of the site rather than a bare error page.
export default function NotFound() {
  return (
    <main id="content" className="page">
      <Breadcrumb />
      <div className="container">
        <section style={{ paddingBlock: "8vh 12vh", textAlign: "center" }}>
          <p className="eyebrow">404</p>
          <h1>Page not found</h1>
          <p className="lead" style={{ marginInline: "auto" }}>
            The page you asked for is not here. It may have moved, or the link
            may be wrong. The corpus and the assistant are both a click away.
          </p>
          <div style={{ display: "flex", gap: 10, justifyContent: "center", marginTop: 24 }}>
            <Link className="btn btn-primary" href="/">Go home</Link>
            <Link className="btn" href="/corpus/">Browse the corpus</Link>
          </div>
        </section>
      </div>
    </main>
  );
}