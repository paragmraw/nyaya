"use client";

import Link from "next/link";
import { useCallback } from "react";
import { usePathname } from "next/navigation";

const FOOTER_LINKS = [
  { href: "/corpus/", label: "Corpus" },
  { href: "/citations/", label: "Citations" },
  { href: "/architecture/", label: "Architecture" },
  { href: "https://github.com/paragmraw/nyaya", label: "GitHub", external: true },
];

export default function Footer() {
  const pathname = usePathname();
  const isHome = (pathname ?? "/") === "/" || (pathname ?? "") === "";

  const backToTop = useCallback(() => {
    const prefersReduced = window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;
    if (prefersReduced) window.scrollTo(0, 0);
    else window.scrollTo({ top: 0, behavior: "smooth" });
  }, []);

  // The home page is a single-viewport layout (overflow: hidden) — no footer.
  // Info pages scroll and show the footer with a back-to-top button.
  if (isHome) return null;

  return (
    <footer className="footer">
      <div className="container footer-inner">
        <nav className="footer-nav" aria-label="Footer navigation">
          {FOOTER_LINKS.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              className="footer-link"
              target={link.external ? "_blank" : undefined}
              rel={link.external ? "noopener noreferrer" : undefined}
            >
              {link.label}
            </Link>
          ))}
        </nav>
        <div className="footer-meta">
          <span className="meta">Nyaya · indexed legal corpus · v0.1 alpha</span>
          <button type="button" className="back-to-top" onClick={backToTop}>
            Back to top ↑
          </button>
        </div>
      </div>
    </footer>
  );
}