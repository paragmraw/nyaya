"use client";

// Collapsible agent trace (plan / reasoning), shared by ChatMessage for both
// the supervisor plan and the Nemotron reasoning stream. Plain-text while
// streaming (see MarkdownContent's plain mode), markdown once final.

import MarkdownContent from "./MarkdownContent";
import { normaliseMd } from "@/lib/markdown";

export type TraceKind = "plan" | "reasoning";

const KIND_LABEL: Record<TraceKind, string> = {
  plan: "Agent plan",
  reasoning: "Reasoning trace",
};

export default function Trace({
  kind,
  text,
  plain = false,
}: {
  kind: TraceKind;
  text: string;
  plain?: boolean;
}) {
  const trimmed = text.trim();
  if (!trimmed) return null;
  return (
    <details className={kind === "plan" ? "plan-trace" : "reasoning"} aria-label={KIND_LABEL[kind]}>
      <summary>{KIND_LABEL[kind]}</summary>
      <div className={kind === "plan" ? "plan-body" : "reasoning-body"}>
        <MarkdownContent text={plain ? trimmed : normaliseMd(trimmed)} plain={plain} />
      </div>
    </details>
  );
}