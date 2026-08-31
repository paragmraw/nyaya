// Page-specific JSON-LD schemas. Pure data (no React imports) so tests can
// import and structurally validate them (see tests/structured-data.test.ts).
//
// Every site-absolute URL is built from SITE (src/lib/site.ts) so identity
// stays single-sourced. URL fragments are only used when a matching DOM id
// exists on the target route — the test asserts this, so a future editor who
// adds `#/some-id` anchors must also add the id (or the test fails).

import { SITE, OG_IMAGE } from "./site";

const ORG_ID = `${SITE}/#organization`;

export const siteSchema = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": ORG_ID,
      name: "Nyaya",
      url: SITE,
      logo: `${SITE}/logo.svg`,
      sameAs: ["https://github.com/paragmraw/nyaya"],
      description:
        "Conversational AI for Indian law - retrieval-grounded legal research assistant.",
    },
    {
      "@type": "WebSite",
      "@id": `${SITE}/#website`,
      url: SITE,
      name: "Nyaya",
      description: "A retrieval-grounded assistant for practicing lawyers.",
      publisher: { "@id": ORG_ID },
      // No SearchAction yet: there is no /search route. Advertising one in
      // JSON-LD sends crawlers to a dead URL (sitelinks searchbox requires
      // the target to exist). Re-add if/when search ships.
    },
  ],
};

// Page-specific schemas
export const homeSchema = {
  "@context": "https://schema.org",
  "@type": ["SoftwareApplication", "WebApplication"],
  name: "Nyaya",
  description: "Conversational AI for Indian law - retrieval-grounded legal research assistant",
  url: `${SITE}/`,
  applicationCategory: "LegalApplication",
  operatingSystem: "Web",
  offers: {
    "@type": "Offer",
    price: "0",
    priceCurrency: "INR",
    availability: "https://schema.org/InStock",
  },
  publisher: {
    "@id": ORG_ID,
  },
  featureList: [
    "Constitutional lookup (464 articles)",
    "CrPC procedure tracer (484 sections)",
    "Penal code (IPC 511 / BNS 358 sections)",
    "Precedent retrieval (5 curated SC judgments)",
    "Drafting assist (planned)",
    "Citation parsing (built into get_section / get_article)",
  ],
  screenshot: `${SITE}${OG_IMAGE}`,
};

export const corpusSchema = {
  "@context": "https://schema.org",
  "@type": "DataCatalog",
  name: "Nyaya Legal Corpus",
  description: "Indexed Indian legal sources including Constitution, statutes, and case law",
  url: `${SITE}/corpus/`,
  publisher: { "@id": ORG_ID },
  dataset: [
    {
      "@type": "Dataset",
      name: "Constitution of India",
      description: "All 464 articles + 12 schedules",
      url: `${SITE}/corpus/`,
      keywords: ["Constitution", "Fundamental Rights", "DPSP", "Amendments"],
      license: "Apache-2.0",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "Bharatiya Nyaya Sanhita, 2023",
      description: "All 358 sections",
      url: `${SITE}/corpus/`,
      keywords: ["BNS", "Criminal Law", "Penal Code"],
      license: "CC BY 4.0",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "Bharatiya Nagarik Suraksha Sanhita, 2023",
      description: "All 531 sections",
      url: `${SITE}/corpus/`,
      keywords: ["BNSS", "Criminal Procedure", "CrPC replacement"],
      license: "CC BY 4.0",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "Bharatiya Sakshya Adhiniyam, 2023",
      description: "All sections (replaces Evidence Act)",
      url: `${SITE}/corpus/`,
      keywords: ["BSA", "Evidence Law", "IEA replacement"],
      license: "CC BY 4.0",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "Indian Penal Code, 1860",
      description: "All 511 sections (legacy reference)",
      url: `${SITE}/corpus/`,
      keywords: ["IPC", "Criminal Law", "Legacy"],
      license: "Public Domain",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "Code of Criminal Procedure, 1973",
      description: "All 484 sections (legacy reference)",
      url: `${SITE}/corpus/`,
      keywords: ["CrPC", "Criminal Procedure", "Legacy"],
      license: "Public Domain",
      creator: { "@id": ORG_ID },
    },
    {
      "@type": "Dataset",
      name: "Supreme Court Landmark Judgments",
      description: "5 curated judgments (Kesavananda, Maneka, Puttaswamy, Shah Bano, Navtej Johar)",
      url: `${SITE}/corpus/`,
      keywords: ["Supreme Court", "Case Law", "Landmark Judgments"],
      license: "Public Domain",
      creator: { "@id": ORG_ID },
    },
  ],
};

export const citationsSchema = {
  "@context": "https://schema.org",
  "@type": ["TechArticle", "HowTo"],
  name: "Nyaya Citation System",
  description: "How Nyaya structures, verifies, and links legal citations",
  url: `${SITE}/citations/`,
  publisher: { "@id": ORG_ID },
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
  url: `${SITE}/architecture/`,
  publisher: { "@id": ORG_ID },
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
  provider: { "@id": ORG_ID },
  softwareVersion: "0.2.0",
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