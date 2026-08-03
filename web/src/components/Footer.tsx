"use client";

import { useCallback } from "react";
import { usePathname } from "next/navigation";

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
        <span className="meta">Nyaya · indexed legal corpus · v0.9 beta</span>
        <button type="button" className="back-to-top" onClick={backToTop}>
          Back to top ↑
        </button>
      </div>
    </footer>
  );
}