"use client";

import { memo } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { isCitationHref } from "@/lib/chat";

// Shared react-markdown component overrides (identical to what ChatMessage
// used to inline): tables get a horizontal-scroll wrapper; inline citation
// links emitted by parseCitations (href prefix /corpus/?act=) render as
// compact chips; other links render with the default .md a styling.
const markdownComponents = {
  table: (props: React.ComponentProps<"table">) => (
    <div className="md-table-wrap">
      <table {...props} />
    </div>
  ),
  a: (props: React.ComponentProps<"a">) =>
    isCitationHref(props.href) ? (
      <a {...props} title={undefined} className="inline-cite" />
    ) : (
      <a {...props}>{props.children ?? props.href}</a>
    ),
};

export type MarkdownContentProps = {
  text: string;
  // Streaming-plain mode: render the raw accumulated text with pre-wrap and
  // NO markdown parse. Used while a message is still streaming — re-parsing
  // growing content through react-markdown every animation frame is the
  // O(n²) that made long answers stutter; the plain render is a single text
  // node swap per frame. Callers flip plain=false on the authoritative final
  // text (correction event / stream end) for the full markdown render.
  plain?: boolean;
};

function MarkdownContentImpl({ text, plain = false }: MarkdownContentProps) {
  if (plain) {
    return (
      <div className="md md-raw" style={{ whiteSpace: "pre-wrap" }}>
        {text}
      </div>
    );
  }
  return (
    <div className="md">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
        {text}
      </ReactMarkdown>
    </div>
  );
}

// Memoized: identical (text, plain) props skip the react-markdown parse
// entirely — the render of a finished message re-uses the previous parse when
// only surrounding state changed.
const MarkdownContent = memo(MarkdownContentImpl);
export default MarkdownContent;