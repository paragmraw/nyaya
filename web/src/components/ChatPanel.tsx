"use client";

// The chat panel is out of scope (per the build brief). We preserve the shell
// so the home-page layout matches the exported design, but lock the body and
// footer behind a CSS blur + pointer-events:none. The header stays visible and
// shows a "Coming soon" pill instead of the "Online" status.
export default function ChatPanel() {
  return (
    <div className="chat-shell chat-locked">
      <div className="chat-head">
        <div className="ch-title">
          <span className="status-dot live" aria-hidden="true" />
          Nyaya Assistant
        </div>
        <div className="ch-meta">
          <span className="pill">Coming soon</span>
          <span className="tag">Citations on</span>
        </div>
      </div>

      <div className="chat-body" id="chatBody" aria-hidden="true">
        <div className="msg bot">
          <div className="avatar">§</div>
          <div className="bubble">
            Namaste. I&apos;m Nyaya — I answer questions on the Constitution, CrPC, IPC/BNS and case law. Ask in plain English or legalese; I&apos;ll cite the exact provision.
            <span className="cite"><strong>Coverage:</strong> Constitution · CrPC 1973 · IPC · BNS/BNSS 2023 · SC &amp; HC judgments</span>
          </div>
        </div>
      </div>

      <div className="chat-foot" aria-hidden="true">
        <div className="composer">
          <textarea
            rows={1}
            aria-label="Ask Nyaya a legal question"
            placeholder="Ask about a provision, section, or case…"
            tabIndex={-1}
            readOnly
          />
          <button className="send" aria-label="Send" tabIndex={-1} disabled>
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round"><path d="M22 2 11 13" /><path d="M22 2 15 22l-4-9-9-4z" /></svg>
          </button>
        </div>
        <div className="composer-hint">
          <span className="status-dot" />
          Retrieval-grounded · not legal advice · verify citations before filing
        </div>
      </div>
    </div>
  );
}