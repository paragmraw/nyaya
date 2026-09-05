"use client";

// Failed-run row inside an assistant bubble: the humanised error message,
// an optional request id for support, and the retry affordance. Extracted
// from ChatMessage.tsx so the retry button markup lives in exactly one place
// (the footer retry in ChatPanel reuses RetryButton instead of its former
// inline-styled duplicate, which also gains the .chat-retry focus/hover CSS).

export function RetryButton({
  onClick,
  disabled = false,
  label = "Retry",
  title = "Retry this message",
  style,
}: {
  onClick: () => void;
  disabled?: boolean;
  label?: string;
  title?: string;
  style?: React.CSSProperties;
}) {
  return (
    <button
      type="button"
      className="chat-retry"
      onClick={onClick}
      disabled={disabled}
      title={title}
      style={style}
    >
      <svg viewBox="0 0 24 24" width="11" height="11" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M21 12a9 9 0 1 1-3-6.7" />
        <path d="M21 3v6h-6" />
      </svg>
      {label}
    </button>
  );
}

export default function ErrorRow({
  message,
  requestId,
  showHalted = false,
  onRetry,
  retryDisabled = false,
}: {
  message: string;
  requestId?: string;
  // True when the turn stopped before producing any content or status, so the
  // row leads with the explicit "stopped, no response" state.
  showHalted?: boolean;
  onRetry?: () => void;
  retryDisabled?: boolean;
}) {
  return (
    <div className="chat-error-row" role="status">
      {showHalted && (
        <span className="chat-status chat-halted">
          <span className="halt-dot" aria-hidden="true" />
          Stopped; no response was generated.
        </span>
      )}
      <span className="chat-error-note">
        {message}
        {requestId ? <span className="chat-rid"> · ref {requestId}</span> : null}
      </span>
      {onRetry !== undefined && (
        <RetryButton onClick={onRetry} disabled={retryDisabled} />
      )}
    </div>
  );
}