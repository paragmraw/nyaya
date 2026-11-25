// Code-verified facts used by the legal/info pages (/privacy, /terms,
// /disclaimer, /faq, /contact, /accessibility).
//
// Every value below is traced to a citation in the fact dossier (workflow
// wf_b107376e-611, verified against the repo on 2026-11-25). Pages import
// these constants instead of hand-copying them so prose cannot drift from
// code; tests/legal-pages.test.ts pins the values. The mirror citations live
// in docs/superpowers/plans/2026-11-25-legal-and-info-pages.md.
//
// Deliberately NOT asserted here (hedged on the pages, not claimed away):
// - Railway platform log retention, NVIDIA prompt-data retention;
// - whether uvicorn access logging is enabled on a given deployment;
// - any operator legal entity (the repo names none — the pages speak as
//   "the nyaya contributors" with the security/contact email below).

export const EFFECTIVE_DATE = "2026-11-25";

/** SECURITY.md:14 — vulnerability + general contact. */
export const CONTACT_EMAIL = "mail@parag.tech";

/** Footer.tsx / Topnav.tsx external link. */
export const REPO_URL = "https://github.com/paragmraw/nyaya";

/** mcp/nyaya/config.py:27-30 (per-minute, per client IP). */
export const RATE_LIMITS = { chat: 15, rest: 120, mcp: 30, analytics: 20 } as const;

/** mcp/nyaya/config.py:31 — streaming body cap. */
export const BODY_CAP_BYTES = 1_048_576;

/** Frontend localStorage/sessionStorage keys:
 *  - chat transcript: web/src/lib/chat/persistence.ts:13 (sessionStorage)
 *  - composer draft:  web/src/components/ChatComposer.tsx:12-15 (sessionStorage)
 *  - theme:           web/src/lib/theme.ts:12 (localStorage) */
export const STORAGE = {
  chatTranscript: "nyaya.chat.v1",
  chatDraft: "nyaya.chat.draft",
  theme: "nyaya-theme",
} as const;

/** chat/nyaya_chat/config.py:41-44 via langchain ChatNVIDIA (llm.py:143-168);
 *  endpoint integrate.api.nvidia.com. ChatPanel.tsx hardcodes the same id. */
export const CHAT_MODEL = "nvidia/nemotron-3.5-lightning-30b-a3b";

/** chat/nyaya_chat/config.py:104 (MAX_HISTORY); frontend matches in
 *  web/src/lib/chat/use-chat.ts:104-110. */
export const HISTORY_TURNS = 8;

/** chat/nyaya_chat/schemas.py:10-36 message max_length; composer mirrors it
 *  with a 4000-char cap (ChatComposer.tsx:5-10). */
export const MESSAGE_MAX_CHARS = 4000;

/** CHANGELOG.md:10 / mcp/README.md:28 — latest published corpus release. */
export const CORPUS_BASELINE = "corpus-v2026.11.25-p1";
