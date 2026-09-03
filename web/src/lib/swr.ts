"use client";

import useSWR from "swr";
import { api, type CorpusStats, type ToolsResponse, type HealthSummary } from "./api";
import corpusStatsFile from "@/data/corpus-stats.json";

// Static build-time snapshot generated from CURATED (src/lib/data.ts) by
// scripts/generate-corpus-stats.ts and committed; useCorpusStats falls back to
// it while /api/corpus-stats is unreachable.
const CORPUS_STATS_FALLBACK = corpusStatsFile as unknown as CorpusStats;

// SWR defaults: 5-min dedup, 10s error retry, no revalidate on focus.
// Static info pages don't need to react to tab focus.
const SWR_OPTS = {
  dedupingInterval: 300_000,
  errorRetryInterval: 10_000,
  errorRetryCount: 2,
  revalidateOnFocus: false,
};

export function useCorpusStats() {
  return useSWR<CorpusStats>("/api/corpus-stats", api.corpusStats, {
    ...SWR_OPTS,
    fallbackData: CORPUS_STATS_FALLBACK,
  });
}

export function useTools(fallbackData?: ToolsResponse) {
  return useSWR<ToolsResponse>("/api/tools", api.tools, { ...SWR_OPTS, fallbackData });
}

export function useHealthSummary(fallbackData?: HealthSummary) {
  return useSWR<HealthSummary>("/api/health-summary", api.healthSummary, { ...SWR_OPTS, fallbackData });
}