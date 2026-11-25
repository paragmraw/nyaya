import type { Metadata } from "next";
import Breadcrumb from "@/components/Breadcrumb";
import { pageOpenGraph } from "@/lib/site";
import {
  BODY_CAP_BYTES,
  CHAT_MODEL,
  CONTACT_EMAIL,
  EFFECTIVE_DATE,
  HISTORY_TURNS,
  MESSAGE_MAX_CHARS,
  RATE_LIMITS,
  STORAGE,
} from "@/lib/legal-facts";

export const metadata: Metadata = {
  title: "Privacy",
  description:
    "What Nyaya handles and does not handle: no accounts, no cookies, no analytics, browser-local chat history, NVIDIA as the chat model processor, and hedged platform-log retention.",
  openGraph: pageOpenGraph({
    title: "Nyaya Privacy - what is and is not collected",
    description:
      "No accounts, no cookies, no analytics; chat transcripts stay in your browser tab and chat messages are sent to NVIDIA's hosted model for processing.",
  }),
};

export default function PrivacyPage() {
  return (
    <main id="content" className="page">
      <div className="container">
        <Breadcrumb />

        {/* hero */}
        <section>
          <p className="eyebrow">Privacy</p>
          <h1>No accounts. No cookies. No analytics.</h1>
          <p className="lead lead-wide">
            This statement describes every place your data touches when you use
            nyaya.parag.tech: your browser, the Nyaya server, and the
            third-party model processor used for chat. Effective{" "}
            <time dateTime="2026-11-25">{EFFECTIVE_DATE}</time>.
          </p>
        </section>

        {/* at a glance */}
        <section className="section">
          <div className="section-head">
            <h2>At a glance</h2>
            <p className="sec-desc">
              The short version, with the detail in the sections below.
            </p>
          </div>
          <div className="stack">
            <p>
              <strong>No accounts.</strong> There is no sign-in, registration,
              or profile system anywhere in the service.
            </p>
            <p>
              <strong>No cookies and no analytics.</strong> The application
              sets none, and none are needed: the only analytics
              instrumentation in the codebase is a development-only console
              reporter, unreachable in production, and there is no analytics
              endpoint: the site makes no requests to advertising,
              analytics, or tracking services.
            </p>
            <p>
              <strong>Chat history is browser-local.</strong> Your
              conversation is kept only in this browser tab&apos;s
              <code> sessionStorage</code> and is cleared when the tab closes;
              the &quot;New chat&quot; button clears it immediately.
            </p>
            <p>
              <strong>Chat messages are processed by a third-party model.</strong>{" "}
              When you chat, your message (plus recent turns) is sent by the
              Nyaya server to NVIDIA&apos;s hosted model API; see{" "}
              <a href="#chat-llm">Chat and the LLM processor</a> below before
              sharing anything sensitive.
            </p>
            <p>
              <strong>The server keeps no conversation records.</strong> There
              is no conversation database on the server; per-request logs carry
              request ids and message lengths, not message text. Platform-level
              log retention is controlled by the hosting provider and is not
              determined by this codebase.
            </p>
          </div>
        </section>

        {/* site usage */}
        <section className="section">
          <div className="section-head">
            <h2>Site usage</h2>
            <p className="sec-desc">What reaches the server, and what it does with it.</p>
          </div>
          <div className="stack">
            <p>
              The application itself builds no profile: corpus retrieval is
              purely lexical lookups over a read-only, locally indexed
              database: no LLM, no history. But like any web request,
              the search terms you enter are sent to the same-origin API as
              URL query strings, and standard HTTP access logging (described
              under <a href="#server-logs">Server logs</a> below)
              may record them alongside your address.
            </p>
            <p>
              Like nearly every internet service, the server sees your IP
              address. The application itself uses it for exactly one thing:
              per-client-IP rate limiting ({RATE_LIMITS.chat} chat requests,{" "}
              {RATE_LIMITS.rest}{" "}
              REST requests, {RATE_LIMITS.mcp} MCP calls, and{" "}
              {RATE_LIMITS.analytics} analytics requests per minute, with a{" "}
              {Math.round(BODY_CAP_BYTES / 1024 / 1024)} MB request-body cap).
              The rate-limit counter lives in a bounded in-process memory
              cache (or, if the operator enables one, a shared Redis counter
              for the same purpose); the standard access log described below
              also records your address.
            </p>
          </div>
        </section>

        {/* browser storage */}
        <section className="section">
          <div className="section-head">
            <h2>Storage on your device</h2>
            <p className="sec-desc">
              Everything the site stores locally, by key.
            </p>
          </div>
          <div className="stack">
            <p>
              <strong>Chat transcript</strong>:{" "}
              <code>{STORAGE.chatTranscript}</code> in{" "}
              <code>sessionStorage</code>: your conversation in the current
              tab. It survives a page reload, is cleared when the tab closes
              (the browser&apos;s standard <code>sessionStorage</code> scope),
              and is overwritten by the &quot;New chat&quot; button. There is
              no per-message delete or export; clearing the tab&apos;s site
              data also removes it.
            </p>
            <p>
              <strong>Composer draft</strong>:{" "}
              <code>{STORAGE.chatDraft}</code> in <code>sessionStorage</code>:
              text typed but not yet sent. The draft is removed on submission
              and cleared with the tab.
            </p>
            <p>
              <strong>Theme preference</strong>:{" "}
              <code>{STORAGE.theme}</code> in <code>localStorage</code>: your
              light/dark choice only.
            </p>
            <p>
              If browser storage is unavailable (for example in a private
              window with site data blocked), the chat and theme still work;
              persistence is simply skipped. That is the full extent of local
              storage: the site does not use IndexedDB or{" "}
              <code>document.cookie</code>.
            </p>
          </div>
        </section>

        {/* chat & LLM processor */}
        <section className="section" id="chat-llm">
          <div className="section-head">
            <h2>Chat and the LLM processor</h2>
            <p className="sec-desc">
              The one part of the service that involves a third party.
            </p>
          </div>
          <div className="stack">
            <p>
              When you send a chat message, the browser posts your message
              together with the preceding {HISTORY_TURNS} turns to the
              same-origin server. To produce a reply, the server builds a
              prompt (the system instructions, your recent history, your
              message, and relevant corpus excerpts retrieved on your behalf)
              and sends it to NVIDIA&apos;s hosted model API
              (integrate.api.nvidia.com, model{" "}
              <code>{CHAT_MODEL}</code>). The active model is displayed in the
              chat panel.
            </p>
            <p>
              A safety classifier in the chat pipeline may also send your raw
              message to the same NVIDIA model when its first-line rules
              cannot classify it automatically.
            </p>
            <p>
              <strong>
                How NVIDIA processes and retains prompt data is governed by
                NVIDIA&apos;s own terms and privacy policy, not by this
                service.
              </strong>{" "}
              You can review them at NVIDIA&apos;s site before using chat. The
              chat message limit is {MESSAGE_MAX_CHARS} characters; if you
              would not share a fact with a third-party model, do not include
              it in a chat message. Corpus search, citation verification, and
              the MCP tools do not involve NVIDIA at all.
            </p>
          </div>
        </section>

        {/* server logs */}
        <section className="section" id="server-logs">
          <div className="section-head">
            <h2>Server logs</h2>
            <p className="sec-desc">What is written, what is not, and what we cannot decide.</p>
          </div>
          <div className="stack">
            <p>
              The chat server does not persist conversations: it holds no
              conversation database or checkpoint store; the client is
              the source of truth for its own history. Application logs record
              request and turn ids, processing phase, latency, token counts,
              and the <em>length</em> of your message and the number of history
              turns. The application&apos;s own logging does not write message
              content; if a request fails, an error report generated at that
              moment could incidentally capture fragments of the failing
              request.
            </p>
            <p>
              The web server component&apos;s standard HTTP access logging is
              not disabled, so request lines (that is, the URL path including
              its query string, so search terms you type into the corpus
              table can appear in them) together with client
              addresses may also be recorded. Where those logs persist and for
              how long is controlled by the hosting platform (Railway), not by
              this codebase; we cannot state or guarantee a retention
              period for them.
            </p>
          </div>
        </section>

        {/* optional tracing */}
        <section className="section">
          <div className="section-head">
            <h2>Optional tracing</h2>
            <p className="sec-desc">A feature that ships disabled.</p>
          </div>
          <div className="stack">
            <p>
              The chat code contains optional LLM observability tracing
              (Langfuse) that, when explicitly configured by an operator, would
              upload model-call inputs and outputs, including chat messages,
              to a configured tracing host. It ships
              <em> disabled</em> in the hosted deployment: no tracing keys are
              configured.
            </p>
          </div>
        </section>

        {/* choices */}
        <section className="section">
          <div className="section-head">
            <h2>Your choices</h2>
            <p className="sec-desc">Control you have right now, in the product.</p>
          </div>
          <div className="stack">
            <p>
              <strong>Clear a conversation:</strong> use &quot;New chat&quot; in
              the chat header, close the tab, or clear site data for this
              origin. There is nothing further on our side to delete, since no
              server-side copy exists.
            </p>
            <p>
              <strong>Check what is stored:</strong> your browser&apos;s
              developer tools show the three keys listed above under
              <em> Application &rarr; Storage</em>; nothing else exists.
            </p>
            <p>
              <strong>Questions:</strong> see the{" "}
              <a href="/contact/">Contact page</a>: messages sent to{" "}
              <code>{CONTACT_EMAIL}</code> are read as part of maintaining
              the project (with a response commitment only for security
              reports, on the terms set out there).
            </p>
          </div>
        </section>

        {/* changes */}
        <section className="section">
          <div className="section-head">
            <h2>Changes</h2>
          </div>
          <p>
            Material changes to this statement will be reflected on this page
            with a new effective date. This document describes the service as
            built (application code and deployed configuration);
            infrastructure decisions outside the codebase are marked as
            controlled by the relevant provider above rather than claimed here.
          </p>
        </section>
      </div>
    </main>
  );
}
