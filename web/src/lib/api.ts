// API client + SWR hooks for the Nyaya SPA.
//
// All data is fetched client-side against same-origin REST endpoints (see
// mcp/nyaya/rest.py). In dev, next.config.mjs rewrites /api/* to localhost:8000;
// in production (static export) the Python container serves everything from
// one origin, so the URLs are identical.

// ─── Types ────────────────────────────────────────────────────────
export type CorpusCounts = {
  acts: number;
  sections: number;
  articles: number;
  judgments: number;
  amendments: number;
  schedules: number;
  chapters: number;
  cross_refs: number;
};

export type CorpusStats = {
  counts: CorpusCounts;
  as_of: string | null;
};

export type Act = {
  short_name: string;
  full_name: string;
  year: number | null;
  citation: string | null;
  kind: "constitution" | "criminal" | "civil" | "commercial" | "judgment";
  source: string;
  source_license: string | null;
  as_of: string | null;
};

export type Judgment = {
  case_name: string;
  citation: string | null;
  court: string;
  date: string | null;
  summary: string | null;
  text: string;
  source: string;
  source_license: string | null;
  as_of: string | null;
};

export type JudgmentsResponse = {
  items: Judgment[];
  total: number;
  limit: number;
  offset: number;
};

export type ToolInfo = {
  name: string;
  description: string;
};

export type ToolsResponse = {
  items: ToolInfo[];
  total: number;
};

export type HealthSummary = {
  status: string;
  counts: Partial<CorpusCounts>;
  as_of: string | null;
};

// ─── Chat ────────────────────────────────────────────────────────
export type ChatHistoryTurn = {
  role: "user" | "assistant";
  content: string;
};

export type ChatRequest = {
  message: string;
  history?: ChatHistoryTurn[];
};

export type ChatCitation = {
  act: string;
  ref: string;
  quote?: string;
};

export type ChatToolEvent = {
  id: string;
  name: string;
  args?: Record<string, unknown>;
  summary?: string;
  state: "start" | "result";
};

export type ChatMessage = {
  id: string;
  role: "user" | "assistant";
  content: string;
  citations: ChatCitation[];
  tools: ChatToolEvent[];
  status?: string;
  error?: string;
  reasoning?: string;
  plan?: string;
};

// ─── Fetch helpers ────────────────────────────────────────────────
const FETCH_TIMEOUT_MS = 10_000;

async function fetchJson<T>(url: string): Promise<T> {
  const controller = new AbortController();
  const timeoutId = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      headers: { Accept: "application/json" },
      signal: controller.signal,
    });
    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText} on ${url}`);
    }
    return (await res.json()) as T;
  } catch (err) {
    if (err instanceof DOMException && err.name === "AbortError") {
      throw new Error(`Request timed out after ${FETCH_TIMEOUT_MS}ms on ${url}`);
    }
    throw err;
  } finally {
    clearTimeout(timeoutId);
  }
}

export const api = {
  corpusStats: () => fetchJson<CorpusStats>("/api/corpus-stats"),
  acts: () => fetchJson<Act[]>("/api/acts"),
  judgments: (limit = 50, offset = 0) =>
    fetchJson<JudgmentsResponse>(`/api/judgments?limit=${limit}&offset=${offset}`),
  tools: () => fetchJson<ToolsResponse>("/api/tools"),
  healthSummary: () => fetchJson<HealthSummary>("/api/health-summary"),
};

// ─── Format helpers ───────────────────────────────────────────────
export function formatNumber(n: number | null | undefined): string {
  if (n == null) return "N/A";
  return n.toLocaleString("en-IN");
}