"use client";

import { useEffect } from "react";

interface WebVitalMetric {
  name: string;
  value: number;
  rating: "good" | "needs-improvement" | "poor";
  delta: number;
  id: string;
}

function sendToAnalytics(metric: WebVitalMetric) {
  // Dev-only console reporting — there is no analytics endpoint, so keep
  // production builds free of any reporter call sites. Next statically
  // replaces NODE_ENV, so the dynamic import below is dead-code-eliminated
  // from production bundles entirely.
  console.log(`[Web Vitals] ${metric.name}:`, {
    value: metric.value.toFixed(2),
    rating: metric.rating,
    delta: metric.delta.toFixed(2),
  });
}

export function initWebVitals() {
  if (typeof window === "undefined") return;
  if (process.env.NODE_ENV !== "development") return;

  // Dynamically import web-vitals to avoid SSR issues.
  // INP replaced the removed-onFID (FID was deprecated and dropped from
  // Core Web Vitals in March 2024 in favour of Interaction to Next Paint).
  import("web-vitals").then(({ onCLS, onINP, onLCP, onTTFB }) => {
    onCLS(sendToAnalytics);
    onINP(sendToAnalytics);
    onLCP(sendToAnalytics);
    onTTFB(sendToAnalytics);
  });
}

export function WebVitals() {
  useEffect(() => {
    initWebVitals();
  }, []);

  return null;
}