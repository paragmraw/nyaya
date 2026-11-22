"use client";

import useSWR from "swr";
import {
  api,
  buildQuery,
  type ActsQuery,
  type ActsResponse,
  type AnalyticsQuery,
  type AnalyticsResponse,
  type CorpusStatusResponse,
  type HealthSummary,
  type JudgmentsQuery,
  type JudgmentsResponse,
  type ToolsResponse,
} from "./api";
import corpusStatsFile from "@/data/corpus-stats.json";

// Static build-time snapshot generated from CURATED & BASELINE_COUNTS
// (scripts/generate-corpus-stats.ts); useCorpusStatus falls back to it while
// /api/corpus-status is unreachable or hydrating.
const CORPUS_STATUS_FALLBACK = corpusStatsFile as unknown as CorpusStatusResponse;

// SWR defaults: 5-min dedup, 10s error retry, no revalidate on focus.
// Static info pages don't need to react to tab focus.
const SWR_OPTS = {
  dedupingInterval: 300_000,
  errorRetryInterval: 10_000,
  errorRetryCount: 2,
  revalidateOnFocus: false,
};

export function useCorpusStatus() {
  return useSWR<CorpusStatusResponse>("/api/corpus-status", api.corpusStatus, {
    ...SWR_OPTS,
    fallbackData: CORPUS_STATUS_FALLBACK,
  });
}

export function useActs(params?: ActsQuery, fallbackData?: ActsResponse) {
  const key = `/api/acts${buildQuery(params)}`;
  return useSWR<ActsResponse>(key, () => api.acts(params), {
    ...SWR_OPTS,
    fallbackData,
  });
}

export function useJudgments(params?: JudgmentsQuery, fallbackData?: JudgmentsResponse) {
  const key = `/api/judgments${buildQuery(params)}`;
  return useSWR<JudgmentsResponse>(key, () => api.judgments(params), {
    ...SWR_OPTS,
    fallbackData,
  });
}

export function useAnalytics(params?: AnalyticsQuery, fallbackData?: AnalyticsResponse) {
  const key = `/api/analytics${buildQuery(params)}`;
  return useSWR<AnalyticsResponse>(key, () => api.analytics(params), {
    ...SWR_OPTS,
    fallbackData,
  });
}

export function useTools(fallbackData?: ToolsResponse) {
  return useSWR<ToolsResponse>("/api/tools", api.tools, { ...SWR_OPTS, fallbackData });
}

export function useHealthSummary(fallbackData?: HealthSummary) {
  return useSWR<HealthSummary>("/api/health-summary", api.healthSummary, { ...SWR_OPTS, fallbackData });
}
