"use client";

import useSWR from "swr";
import { api, type Act, type CorpusStats, type JudgmentsResponse, type ToolsResponse, type HealthSummary } from "./api";

// SWR defaults: 5-min dedup, 10s error retry, no revalidate on focus.
// Static info pages don't need to react to tab focus.
const SWR_OPTS = {
  dedupingInterval: 300_000,
  errorRetryInterval: 10_000,
  errorRetryCount: 2,
  revalidateOnFocus: false,
};

export function useCorpusStats() {
  return useSWR<CorpusStats>("/api/corpus-stats", api.corpusStats, SWR_OPTS);
}

export function useActs() {
  return useSWR<Act[]>("/api/acts", api.acts, SWR_OPTS);
}

export function useJudgments(limit = 50, offset = 0) {
  return useSWR<JudgmentsResponse>(
    `/api/judgments?limit=${limit}&offset=${offset}`,
    () => api.judgments(limit, offset),
    SWR_OPTS,
  );
}

export function useTools() {
  return useSWR<ToolsResponse>("/api/tools", api.tools, SWR_OPTS);
}

export function useHealthSummary() {
  return useSWR<HealthSummary>("/api/health-summary", api.healthSummary, SWR_OPTS);
}