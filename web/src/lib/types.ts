// Shared type definitions for the Nyaya web app.
//
// Re-exports API types from api.ts so pages can import all types from one
// place (`@/lib/types`), and defines page/component-local metadata types.

import type { ReactNode } from "react";

export type {
  CorpusCounts,
  CorpusLicensing,
  CorpusSourceStatus,
  CorpusStatusResponse,
  ActSummary,
  ActsQuery,
  ActsResponse,
  JudgmentSummary,
  JudgmentsQuery,
  JudgmentsResponse,
  AnalyticsQuery,
  AnalyticsRow,
  AnalyticsResponse,
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

export type ResourceItem = { uri: string; desc: string };

export type PromptItem = { name: string; desc: string };

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
export type CorpusCategory = "judiciary" | "legislation" | "regulators" | "tribunals";

export type CorpusCategoryFilter = "all" | CorpusCategory | "commercial_safe";

export type CuratedRow = {
  short_name: string;
  aliases?: string[];
  name: string;
  type: string;
  category: CorpusCategory;
  tier: string;
  coverage: string;
  status: "live" | "beta" | "coming";
  license_spdx: string;
  commercial_safe: boolean;
  source: string;
  fallback_date: string;
};
