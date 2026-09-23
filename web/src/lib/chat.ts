"use client";

// Chat library facade. The implementation moved from this 745-line monolith
// into web/src/lib/chat/, one module per concern:
//
//   chat/errors.ts          — humanizeError (machine codes → user copy)
//   chat/citations.ts       — CITE_RE, parseCitations, stripCitationMarkers,
//                             CITE_HREF_PREFIX, isCitationHref
//   chat/citation-stream.ts — StreamingCitationStripper (incremental,
//                             linear-time streaming marker→chip conversion)
//   chat/persistence.ts     — serialize/deserialize + sessionStorage store
//                             (write is debounced by useChat, see use-chat.ts)
//   chat/sse.ts             — parseSseBlock + createFrameBatcher (rAF coalescing)
//   chat/retry.ts           — trimForRetry + uid
//   chat/session.ts         — createChatSession: the headless streaming state
//                             machine (fetch, SSE loop, batching, tools,
//                             correction rebase, errors, timeout, cancel)
//   chat/use-chat.ts        — useChat: the thin React adapter
//
// All existing imports (ChatPanel, MessageList, the 12 test suites) keep
// importing from here unchanged.

export * from "./chat/errors";
export * from "./chat/citations";
export * from "./chat/citation-stream";
export * from "./chat/persistence";
export * from "./chat/sse";
export * from "./chat/retry";
export * from "./chat/session";
export * from "./chat/use-chat";