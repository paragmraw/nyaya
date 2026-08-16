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
  // In production, send to your analytics endpoint
  // For now, log to console in development
  if (process.env.NODE_ENV === "development") {
    console.log(`[Web Vitals] ${metric.name}:`, {
      value: metric.value.toFixed(2),
      rating: metric.rating,
      delta: metric.delta.toFixed(2),
    });
  }
}

export function initWebVitals() {
  if (typeof window === "undefined") return;

  // Dynamically import web-vitals to avoid SSR issues
  import("web-vitals").then(({ onCLS, onFID, onLCP, onTTFB, onINP }) => {
    onCLS(sendToAnalytics);
    onFID(sendToAnalytics);
    onLCP(sendToAnalytics);
    onTTFB(sendToAnalytics);
    onINP(sendToAnalytics);
  });
}

export function WebVitals() {
  useEffect(() => {
    initWebVitals();
  }, []);

  return null;
}