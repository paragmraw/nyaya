import type { Metadata } from "next";
import Breadcrumb from "@/components/Breadcrumb";
import { pageOpenGraph } from "@/lib/site";
import { CONTACT_EMAIL } from "@/lib/legal-facts";

export const metadata: Metadata = {
  title: "Accessibility",
  description:
    "Nyaya's accessibility statement: implemented supports (skip link, focus management, reduced motion, screen-reader live regions), WCAG 2.2 AA target, known limitations, and how to report barriers.",
  openGraph: pageOpenGraph({
    title: "Nyaya Accessibility Statement",
    description:
      "What Nyaya does for keyboard, screen-reader and reduced-motion users, what it does not yet do, and where to report a barrier.",
  }),
};

export default function AccessibilityPage() {
  return (
    <main id="content" className="page">
      <div className="container">
        <Breadcrumb />

        {/* hero */}
        <section>
          <p className="eyebrow">Accessibility</p>
          <h1>Accessibility statement</h1>
          <p className="lead lead-wide">
            Nyaya is designed to follow the practices of WCAG 2.2 Level AA:
            keyboard navigation is a design requirement, content is served to
            screen readers from semantic HTML, and the interface avoids
            relying on colour alone (statuses always carry a text label).
            <strong>We have not commissioned a formal
            third-party conformance audit</strong>, so this page describes
            what is implemented and what is known to fall short, not a
            certification. If you hit a barrier, we want to hear about it:{" "}
            <code>{CONTACT_EMAIL}</code>.
          </p>
        </section>

        {/* implemented */}
        <section className="section">
          <div className="section-head">
            <h2>What is implemented</h2>
            <p className="sec-desc">Concrete features you can use right now.</p>
          </div>
          <div className="stack">
            <p>
              <strong>Keyboard access.</strong> A &quot;Skip to main
              content&quot; link is the first focusable element on every page
              and jumps to the main region. Escape closes the mobile
              navigation with a Tab focus-trap while it is open, and the
              pipeline-stage cards on the <a href="/architecture/">
              Architecture page</a> expand with Enter or Space. The
              back-to-top footer control respects{" "}
              <code>prefers-reduced-motion</code>.
            </p>
            <p>
              <strong>Screen readers.</strong> Pages use semantic landmarks
              (header <code>nav</code>, <code>main</code>, breadcrumb{" "}
              <code>nav</code>) with labelled regions; navigation links mark
              the active page with <code>aria-current=&quot;page&quot;</code>.
              The chat panel labels its controls with{" "}
              <code>aria-label</code> and announces streaming status phases
              and completion through an off-screen <code>aria-live</code>{" "}
              region; once done, the reply is read from the messages list
              like any other content. When a message nears the cap, the
              remaining character count is exposed as text: currently
              once fewer than 500 characters remain, before the
              4000-character cap stops further input.
            </p>
            <p>
              <strong>Visual design.</strong> The interface automatically
              follows your operating system&apos;s{" "}
              <code>prefers-color-scheme</code> light/dark setting, with an
              explicit toggle that persists locally (that preference is the
              only <code>localStorage</code> entry; see the{" "}
              <a href="/privacy/">Privacy statement</a>). Colour contrast and
              focus indicators are handled in the theme, and statuses carry
              text labels so state does not depend on colour alone.
            </p>
            <p>
              <strong>Motion and behaviour.</strong> Animated transitions are
              limited and respect reduced-motion preferences; the interface
              does not flash, autoplay moving content, or impose time limits
              on interaction. Chat offers explicit cancel and retry controls,
              and its progress state is announced as it changes.
            </p>
          </div>
        </section>

        {/* known limitations */}
        <section className="section">
          <div className="section-head">
            <h2>Known limitations</h2>
            <p className="sec-desc">Stated plainly, not hidden.</p>
          </div>
          <div className="stack">
            <p>
              <strong>The interface is English-only</strong> (<code>lang=&quot;en&quot;</code>{" "}
              throughout) and its webfonts ship Latin-only subsets: Devanagari
              and other vernacular strings that appear inside judgment texts
              render with your system&apos;s fallback fonts, which may look
              inconsistent with the rest of the interface.
            </p>
            <p>
              <strong>Scanned, image-only court PDFs are not transcribed by
              default.</strong> Documents that are effectively images are
              rejected by the extractor unless opt-in Apache-2.0 OCR
              (<code>NYAYA_ENABLE_OCR=1</code>) is enabled, so on the hosted
              service their content is not text-accessible at all, an
              accuracy-versus-availability trade-off, documented on the{" "}
              <a href="/disclaimer/">Disclaimer page</a>.
            </p>
            <p>
              <strong>Complex legal jargon is not simplified.</strong> The
              citation anatomy and architecture material assumes practitioner
              reading level; a plain-language glossary is a known documentation
              gap rather than a shipped feature.
            </p>
          </div>
        </section>

        {/* feedback */}
        <section className="section">
          <div className="section-head">
            <h2>Report a barrier</h2>
          </div>
          <div className="stack">
            <p>
              Email <code>{CONTACT_EMAIL}</code> with the page you were on, a
              short description of what blocked you, and your assistive
              technology (screen reader, magnifier, switch device) if known.
              Accessibility reports are treated the same way as other
              corrections; there is no rapid-response commitment
              beyond the security window, but they are read and acted on. See
              the <a href="/contact/">Contact page</a> for the full channel
              list.
            </p>
          </div>
        </section>
      </div>
    </main>
  );
}
