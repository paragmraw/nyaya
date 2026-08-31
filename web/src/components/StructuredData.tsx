"use client";

import Script from "next/script";

// The page schemas themselves live in src/lib/schema.ts (pure data, so the
// structural JSON-LD tests can import them without pulling in React). They
// are re-exported here for back-compat with the existing page imports.
export {
  siteSchema,
  homeSchema,
  corpusSchema,
  citationsSchema,
  architectureSchema,
  faqSchema,
} from "@/lib/schema";

interface StructuredDataProps {
  data: Record<string, unknown>;
}

export default function StructuredData({ data }: StructuredDataProps) {
  return (
    <Script
      id="page-schema"
      type="application/ld+json"
      dangerouslySetInnerHTML={{ __html: JSON.stringify(data) }}
      strategy="lazyOnload"
    />
  );
}