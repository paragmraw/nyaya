"use client";

import Script from "next/script";

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

// Page-specific schemas
export const homeSchema = {
  "@context": "https://schema.org",
  "@type": ["SoftwareApplication", "WebApplication"],
  name: "Nyaya",
  description: "Conversational AI for Indian law - retrieval-grounded legal research assistant",
  url: "https://nyaya.example.com/",
  applicationCategory: "LegalApplication",
  operatingSystem: "Web",
  offers: {
    "@type": "Offer",
    price: "0",
    priceCurrency: "INR",
    availability: "https://schema.org/InStock",
  },
  publisher: {
    "@id": "https://nyaya.example.com/#organization",
  },
  featureList: [
    "Constitutional lookup (395 articles)",
    "CrPC procedure tracer (484 sections)",
    "Penal code (IPC 511 / BNS 358 sections)",
    "Precedent retrieval (5 curated SC judgments)",
    "Drafting assist (planned)",
    "Citation resolver",
  ],
  screenshot: "https://nyaya.example.com/og-default.svg",
};

export const corpusSchema = {
  "@context": "https://schema.org",
  "@type": "DataCatalog",
  name: "Nyaya Legal Corpus",
  description: "Indexed Indian legal sources including Constitution, statutes, and case law",
  url: "https://nyaya.example.com/corpus/",
  publisher: {
    "@id": "https://nyaya.example.com/#organization",
  },
  dataset: [
    {
      "@type": "Dataset",
      name: "Constitution of India",
      description: "All 395 articles + 12 schedules",
      url: "https://nyaya.example.com/corpus/#constitution",
      keywords: ["Constitution", "Fundamental Rights", "DPSP", "Amendments"],
      license: "Apache-2.0",
      creator: { "@id": "https://nyaya.example.com/#organization" },
    },
    {
      "@type": "Dataset",
      name: "Bharatiya Nyaya Sanhita, 2023",
      description: "All 358 sections",
      url: "https://nyaya.example.com/corpus/#bns",
      keywords: ["BNS", "Criminal Law", "Penal Code"],
      license: "CC BY 4.0",
      creator: { "@id": "https://nyaya.example.com/#organization" },
    },
    {
      "@type": "Dataset",
      name: "Bharatiya Nagarik Suraksha Sanhita, 2023",
      description: "All 531 sections",
      url: "https://nyaya.example.com/corpus/#bnss",
      keywords: ["BNSS", "Criminal Procedure", "CrPC replacement"],
      license: "CC BY 4.0",
      creator: { "@id": "https://nyaya.example.com/#organization" },
    },
    {
      "@type": "Dataset",
      name: "Bharatiya Sakshya Adhiniyam, 2023",
      description: "All sections (replaces Evidence Act)",
      url: "https://nyaya.example.com/corpus/#bsa",
      keywords: ["BSA", "Evidence Law", "IEA replacement"],
      license: "CC BY 4.0",
      creator: { "@id": "https://nyaya.example.com/#organization" },
    },
    {
      "@type": "Dataset",
      name: "Indian Penal Code, 1860",
      description: "All 511 sections (legacy reference)",
      url: "https://nyaya.example.com/corpus/#ipc",
      keywords: ["IPC", "Criminal Law", "Legacy"],
      license: "Public Domain",
      creator: { "@id": "https://nyaya.example.com/#organization" },
    },
    {
      "@type": "Dataset",
      name: "Code of Criminal Procedure, 1973",
      description: "All 484 sections (legacy reference)",
      url: "https://nyaya.example.com/corpus/#crpc",
      keywords: ["CrPC", "Criminal Procedure", "Legacy"],
      license: "Public Domain",
      creator: { "@id": "https://nyaya.example.com/#organization" },
    },
    {
      "@type": "Dataset",
      name: "Supreme Court Landmark Judgments",
      description: "5 curated judgments (Kesavananda, Maneka, Puttaswamy, Shah Bano, Navtej Johar)",
      url: "https://nyaya.example.com/corpus/#judgments",
      keywords: ["Supreme Court", "Case Law", "Landmark Judgments"],
      license: "Public Domain",
      creator: { "@id": "https://nyaya.example.com/#organization" },
    },
  ],
};

export const citationsSchema = {
  "@context": "https://schema.org",
  "@type": ["TechArticle", "HowTo"],
  name: "Nyaya Citation System",
  description: "How Nyaya structures, verifies, and links legal citations",
  url: "https://nyaya.example.com/citations/",
  publisher: {
    "@id": "https://nyaya.example.com/#organization",
  },
  about: {
    "@type": "Thing",
    name: "Legal Citation Format",
    description: "Standardized citation formats for Indian law",
  },
  step: [
    {
      "@type": "HowToStep",
      position: 1,
      name: "Parse",
      text: "Extract citation strings from the draft answer: article numbers, section references, case names.",
      itemListElement: {
        "@type": "HowToDirection",
        text: "Parse \"Art. 21\" → {type: constitution, art: 21}",
      },
    },
    {
      "@type": "HowToStep",
      position: 2,
      name: "Match",
      text: "Look up each parsed citation in the indexed corpus to confirm it exists and the text matches.",
      itemListElement: {
        "@type": "HowToDirection",
        text: "match(\"S. 41A\", \"CrPC 1973\") → found, section_text",
      },
    },
    {
      "@type": "HowToStep",
      position: 3,
      name: "Fetch",
      text: "Pull the full provision or judgment text from the corpus so the citation can be displayed inline.",
      itemListElement: {
        "@type": "HowToDirection",
        text: "fetch(\"Art. 21\") → Constitution article text",
      },
    },
    {
      "@type": "HowToStep",
      position: 4,
      name: "Display",
      text: "Render the citation as a link the user can click to open the source card with the full text and provenance.",
      itemListElement: {
        "@type": "HowToDirection",
        text: "→ Source card: Constitution of India, Art. 21",
      },
    },
  ],
};

export const architectureSchema = {
  "@context": "https://schema.org",
  "@type": ["SoftwareApplication", "APIReference"],
  name: "Nyaya Architecture",
  description: "Four-stage retrieval-grounded pipeline for Indian legal AI",
  url: "https://nyaya.example.com/architecture/",
  publisher: {
    "@id": "https://nyaya.example.com/#organization",
  },
  applicationCategory: "DeveloperApplication",
  operatingSystem: "Cloud",
  featureList: [
    "Query understanding via Supervisor LLM",
    "Parallel tool execution (semantic_query, get_section, get_article)",
    "Reranking of retrieved passages",
    "Synthesis LLM with citation constraints",
    "Citation resolution and verification",
    "MCP server for editor integration",
  ],
  provider: {
    "@id": "https://nyaya.example.com/#organization",
  },
  softwareVersion: "0.1.0-alpha",
  releaseNotes: "https://github.com/paragmraw/nyaya/releases",
};

export const faqSchema = {
  "@context": "https://schema.org",
  "@type": "FAQPage",
  mainEntity: [
    {
      "@type": "Question",
      name: "Does Nyaya cite unreported judgments?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "No. Orders and judgments not published in a reported journal are not in the corpus. Nyaya will say it cannot find a citation rather than invent one.",
      },
    },
    {
      "@type": "Question",
      name: "Are subordinate legislation and rules indexed?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "Not yet. Notifications, rules, and subordinate legislation under major statutes are not yet indexed. Coming per the corpus roadmap.",
      },
    },
    {
      "@type": "Question",
      name: "Are Pre-1950 Privy Council decisions included?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "Judgments of the Privy Council (the apex court for British India until 1950) are not yet indexed. This is planned for a future release.",
      },
    },
    {
      "@type": "Question",
      name: "Does Nyaya track overruled or modified judgments?",
      acceptedAnswer: {
        "@type": "Answer",
        text: "No. Nyaya does not yet track whether a judgment has been overruled or modified by a later bench. Always confirm currency before filing.",
      },
    },
  ],
};