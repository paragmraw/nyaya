// Single source of truth for site identity (plan user-decision: "one SITE
// constant feeds sitemap, OG, JSON-LD, breadcrumbs").
//
// `next-sitemap.config.cjs` cannot import this TS module (plain CJS run by
// npx), so its siteUrl is duplicated there — keep the two in sync until P5's
// toolchain cleanup makes this importable.

export const SITE = "https://nyaya.parag.tech";

// API/server version displayed in UI copy (mirrors __version__ in
// mcp/nyaya/__init__.py — Python is the canonical source for the backend).
export const API_VERSION = "0.2.0";

export const OG_IMAGE = "/og-default.png";

/**
 * openGraph block for a page. Next replaces (not deep-merges) the layout
 * openGraph when a page defines its own, which silently dropped the default
 * og:image — this helper keeps images (and type) present on every page.
 */
export function pageOpenGraph(opts: { title: string; description: string }) {
  return {
    type: "website" as const,
    title: opts.title,
    description: opts.description,
    images: [
      {
        url: OG_IMAGE,
        width: 1200,
        height: 630,
        alt: "Nyaya - Indian Law AI Assistant",
      },
    ],
  };
}