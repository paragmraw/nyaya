// Single source of truth for site identity (plan user-decision: "one SITE
// constant feeds sitemap, OG, JSON-LD, breadcrumbs").
//
// `next-sitemap.config.cjs` cannot import this TS module (plain CJS run by
// npx), so its siteUrl is duplicated there; keep the two in sync.

export const SITE = "https://nyaya.parag.tech";

// API/server version displayed in UI copy (mirrors Nyaya v2.1.0 release).
export const API_VERSION = "2.1.0";

export const OG_IMAGE = "/og-default.png";

/**
 * openGraph block for a page. Next replaces (not deep-merges) the layout
 * openGraph when a page defines its own, which silently dropped the default
 * og:image. This helper keeps images (and type) present on every page.
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
        alt: "Nyaya - Indian Law MCP & Legal Research Assistant",
      },
    ],
  };
}
