// Shared type definitions for the Nyaya web app.
//
// Re-exports API types from api.ts so pages can import all types from one
// place (`@/lib/types`), and defines page/component-local types that were
// previously inlined in architecture/page.tsx and citations/page.tsx.

import type { ReactNode } from "react";

export type {
  CorpusCounts,
  CorpusStats,
  ToolInfo,
  ToolsResponse,
  HealthSummary,
  ChatHistoryTurn,
  ChatRequest,
  ChatCitation,
  ChatToolEvent,
  ChatMessage,
} from "./api";

// ─── architecture/page.tsx ────────────────────────────────────────
export type StackRow = { name: string; role: string; tool: string };

export type FlowStep = { num: string; text: ReactNode };

export type OpennessItem = { on: boolean; text: string; meta: string };

// ─── citations/page.tsx ────────────────────────────────────────────
export type FormatCard = {
  type: string;
  format: string;
  desc: string;
  url: string;
  urlLabel: string;
};

export type PipeStep = { num: string; title: string; desc: string; eg: string };

export type Limit = { mark: string; title: string; sub: string };

// ─── CapTable.tsx ──────────────────────────────────────────────────
export type Cap = {
  code: string;
  name: string;
  desc: string;
  coverage: string;
};

// ─── CorpusTable.tsx ──────────────────────────────────────────────
export type CuratedRow = {
  short_name: string;
  name: string;
  type: string;
  coverage: string;
  status: "live" | "beta" | "coming";
  fallback_date: string;
};