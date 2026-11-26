// API client + types for the Nyaya v2.1 SPA.
//
// All data is fetched client-side against same-origin REST endpoints (see
// mcp/nyaya/rest.py). In dev, next.config.mjs rewrites /api/* to localhost:8000;
// in production (static export) the Python container serves everything from
// one origin, so the URLs are identical.

// ─── Types ────────────────────────────────────────────────────────
export type CorpusCounts = {
  legislation_jurisdictions: number;
  central_acts: number;
  state_acts: number;
  regulators: number;
  tribunals: number;
  provisions: number;
  articles: number;
  schedules: number;
  amendments: number;
  concordance_mappings: number;
  judicial_invalidations: number;
  sc_judgments: number;
  hc_judgments: number;
  tribunal_orders: number;
  district_court_records: number;
  citation_edges: number;
};

export type CorpusLicensing = {
  code_license: string;
  commercial_safe_default: boolean;
  nc_datasets: string[];
};

export type CorpusSourceStatus = {
  source_id?: string;
  short_name?: string;
  name?: string;
  category?: string;
  coverage?: string;
  records?: number;
  record_count?: number;
  year_coverage?: string | null;
  text_availability?: string;
  license_spdx?: string;
  commercial_safe?: boolean;
  terms_status?: string;
  as_of?: string | null;
  provenance?: {
    source_id?: string;
    source_name?: string;
    source_url?: string;
    license_spdx?: string;
    commercial_safe?: boolean;
    terms_status?: string;
    as_of?: string | null;
  };
};

export type CorpusStatusResponse = {
  version?: string;
  service_version?: string;
  retrieval_mode?: string;
  commercial_safe_locked?: boolean;
  as_of: string | null;
  tiers?: Record<string, unknown>;
  counts: CorpusCounts;
  licensing?: CorpusLicensing;
  sources?: CorpusSourceStatus[];
};

export type ActSummary = {
  short_name: string;
  full_name: string;
  jurisdiction?: string;
  year?: number | null;
  category?: string;
  kind?: string;
  status?: string;
  provision_count?: number;
  source?: string;
  license_spdx?: string;
  commercial_safe?: boolean;
  as_of?: string | null;
};

export type ActsQuery = {
  jurisdiction?: string;
  category?: string;
  q?: string;
  limit?: number;
  offset?: number;
  commercial_safe_only?: boolean;
};

export type ActsResponse = {
  items: ActSummary[];
  total: number;
  limit?: number;
  offset?: number;
};

export type JudgmentSummary = {
  case_id: string;
  cnr?: string | null;
  neutral_citation?: string | null;
  reporter_citations?: string[];
  title: string;
  court: string;
  court_tier?: string;
  decision_date?: string | null;
  bench_strength?: number | null;
  disposal_nature?: string | null;
  text_availability?: string;
  license_spdx?: string;
  commercial_safe?: boolean;
};

export type JudgmentsQuery = {
  tier?: string;
  court?: string;
  year_from?: number;
  year_to?: number;
  q?: string;
  limit?: number;
  offset?: number;
  commercial_safe_only?: boolean;
};

export type JudgmentsResponse = {
  items: JudgmentSummary[];
  total: number;
  total_is_estimate?: boolean;
  limit: number;
  offset: number;
};

export type AnalyticsQuery = {
  dataset?: string;
  group_by?: string;
  court?: string;
  state?: string;
  year_from?: number;
  year_to?: number;
  top_n?: number;
  commercial_safe_only?: boolean;
};

export type AnalyticsRow = {
  key: string;
  count: number;
  share_pct?: number;
};

export type AnalyticsResponse = {
  dataset: string;
  group_by: string;
  rows: AnalyticsRow[];
  total_cases: number;
  as_of: string | null;
  commercial_safe: boolean;
  license_spdx: string;
};

export type ToolInfo = {
  name: string;
  description: string;
  open_world?: boolean;
};

export type ToolsResponse = {
  items: ToolInfo[];
  total: number;
  resources_count?: number;
  prompts_count?: number;
};

export type HealthSummary = {
  status: string;
  version?: string;
  counts: Partial<CorpusCounts>;
  backends?: Record<string, unknown>;
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
  requestId?: string;
  // True once the content is the authoritative verified text (correction
  // received or stream finished). While false during an active stream the
  // bubble renders streaming-plain text (no per-frame markdown parse).
  contentFinal?: boolean;
};

// ─── Fetch helpers ────────────────────────────────────────────────
const FETCH_TIMEOUT_MS = 10_000;

export function buildQuery(params?: Record<string, string | number | boolean | undefined | null>): string {
  if (!params) return "";
  const sp = new URLSearchParams();
  for (const [key, val] of Object.entries(params)) {
    if (val === undefined || val === null || val === "") continue;
    sp.set(key, String(val));
  }
  const qs = sp.toString();
  return qs ? `?${qs}` : "";
}

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
  corpusStatus: () => fetchJson<CorpusStatusResponse>("/api/corpus-status"),
  acts: (params?: ActsQuery) => fetchJson<ActsResponse>(`/api/acts${buildQuery(params)}`),
  judgments: (params?: JudgmentsQuery) => fetchJson<JudgmentsResponse>(`/api/judgments${buildQuery(params)}`),
  analytics: (params?: AnalyticsQuery) => fetchJson<AnalyticsResponse>(`/api/analytics${buildQuery(params)}`),
  tools: () => fetchJson<ToolsResponse>("/api/tools"),
  healthSummary: () => fetchJson<HealthSummary>("/api/health-summary"),
};

// ─── Format helpers ───────────────────────────────────────────────
export function formatNumber(n: number | null | undefined): string {
  if (n == null) return "N/A";
  return n.toLocaleString("en-IN");
}

export function formatCompactCount(n: number | null | undefined): string {
  if (n == null) return "N/A";
  if (n >= 1_000_000) {
    const m = n / 1_000_000;
    const str = Number.isInteger(m) ? String(m) : m.toFixed(2).replace(/\.?0+$/, "");
    return `${str}M+`;
  }
  if (n >= 1_000) {
    const k = n / 1_000;
    const str = Number.isInteger(k) ? String(k) : k.toFixed(1).replace(/\.0$/, "");
    return `${str}k+`;
  }
  return n.toLocaleString("en-IN");
}
