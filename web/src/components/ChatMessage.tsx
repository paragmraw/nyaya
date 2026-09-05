"use client";

// ChatMessageView: renders a single chat turn (user or assistant). Since the
// P4 decomposition this is a thin composition layer — the tool chips
// (ToolChips), traces (Trace), citations (CitationList), error row
// (ErrorRow) and markdown rendering (MarkdownContent) each live in their own
// component; this file owns only the per-message layout and the
// streaming-plain decision.
//
// Memoized: during streaming only the tail message's object identity changes
// (updateAssistant replaces just that entry), so earlier bubbles skip
// re-render entirely — their props keep identity. The streaming tail always
// re-renders (its msg object changes each flush).

import { memo, useMemo } from "react";
import { BalanceIcon, PersonIcon } from "./icons";
import MarkdownContent from "./MarkdownContent";
import ToolChips from "./ToolChips";
import Trace from "./Trace";
import CitationList from "./CitationList";
import ErrorRow from "./ErrorRow";
import { normaliseMd } from "@/lib/markdown";
import type { ChatMessage } from "@/lib";

// Map backend status codes to user-friendly phase labels. Exported because
// ChatPanel reuses it for the streaming tail's aria-live announcements.
export function phaseLabel(status: string): string {
  const labels: Record<string, string> = {
    thinking: "Thinking…",
    analyzing: "Analysing your question…",
    searching: "Searching legal corpus…",
    composing: "Composing answer…",
  };
  return labels[status] ?? `${status}…`;
}

function ChatMessageViewImpl({ msg, isStreaming = false, onRetry }: { msg: ChatMessage; isStreaming?: boolean; onRetry?: () => void }) {
  const isBot = msg.role === "assistant";
  // Streaming-plain: while this bubble is actively streaming and its content
  // is not yet the authoritative verified text, render raw pre-wrap text —
  // react-markdown re-parsing growing content per animation frame is O(n²).
  // The plan/reasoning traces (which grow for hundreds of deltas before the
  // answer starts) get the same treatment.
  const streamingPlain = isBot && isStreaming && !msg.contentFinal;
  // Memoize normaliseMd — it's O(n) and the full content can be long.
  // Skipped entirely during streaming-plain (raw display needs no markdown
  // normalisation); recomputed once when the final text lands.
  const normalisedContent = useMemo(
    () => (streamingPlain ? msg.content : normaliseMd(msg.content)),
    [msg.content, streamingPlain],
  );
  return (
    <div className={`msg ${isBot ? "bot" : msg.role}`}>
      <div className="avatar" aria-hidden="true">
        {isBot ? <BalanceIcon /> : <PersonIcon />}
      </div>
      <div className="bubble" data-error={msg.error ? "" : undefined}>
        {/* Phase status indicator (shown while streaming with no content yet) */}
        {isBot && !msg.content && (msg.status || (isStreaming && msg.tools.length === 0 && !msg.reasoning)) && (
          <span className="chat-status chat-phase">
            <span className="phase-spinner" aria-hidden="true" />
            {msg.status ? phaseLabel(msg.status) : "Thinking…"}
          </span>
        )}

        {/* Tool calls — shown above content for prominence during streaming */}
        {isBot && <ToolChips tools={msg.tools} />}

        {/* Content (streams in token-by-token; plain while streaming,
            markdown once the verified final text lands) */}
        {isBot ? (
          msg.content ? (
            <MarkdownContent text={normalisedContent} plain={streamingPlain} />
          ) : null
        ) : (
          msg.content
        )}

        {/* Failed run: humanised message (see humanizeError in chat.ts), an
            optional request id for support, and a retry affordance inside the
            failed assistant bubble itself. */}
        {isBot && msg.error && (
          <ErrorRow
            message={msg.error}
            requestId={msg.requestId}
            showHalted={!msg.content && !msg.status}
            onRetry={onRetry}
            retryDisabled={isStreaming}
          />
        )}

        {/* Agent plan (supervisor's reasoning) and reasoning trace — plain
            while streaming (they grow for hundreds of deltas), markdown after */}
        {isBot && msg.plan && msg.plan.trim() && (
          <Trace kind="plan" text={msg.plan} plain={streamingPlain} />
        )}
        {isBot && msg.reasoning && msg.reasoning.trim() && (
          <Trace kind="reasoning" text={msg.reasoning} plain={streamingPlain} />
        )}

        {/* Citations */}
        {isBot && <CitationList citations={msg.citations} />}
      </div>
    </div>
  );
}

const ChatMessageView = memo(ChatMessageViewImpl);
export default ChatMessageView;