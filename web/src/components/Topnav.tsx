"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useState } from "react";

const NAV_LINKS = [
  { href: "/corpus", label: "Corpus" },
  { href: "/citations", label: "Citations" },
  { href: "/architecture", label: "Architecture" },
];

export default function Topnav() {
  const pathname = usePathname();
  const current = (pathname ?? "/").replace(/\/$/, "") || "/";
  const isHome = current === "/";
  const [menuOpen, setMenuOpen] = useState(false);

  // Close the mobile menu on route change.
  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  // Close the mobile menu on Escape.
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") setMenuOpen(false);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  const toggleMenu = useCallback(() => setMenuOpen((v) => !v), []);

  return (
    <header className={`topnav${isHome ? "" : " topnav-sticky"}`}>
      <div className="container topnav-inner">
        <Link href="/" className="logo" aria-label="Nyaya">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.svg" alt="Nyaya" />
        </Link>
        <nav>
          {NAV_LINKS.map((l) => {
            const isActive = current === l.href || current.startsWith(l.href + "/");
            return (
              <Link
                key={l.href}
                href={l.href}
                className={isActive ? "active" : ""}
                aria-current={isActive ? "page" : undefined}
              >
                {l.label}
              </Link>
            );
          })}
        </nav>
        <div className="nav-right">
          <span className="meta">v0.1 · alpha</span>
          <button
            type="button"
            className="menu-toggle"
            aria-label="Toggle navigation menu"
            aria-expanded={menuOpen}
            aria-controls="mobile-nav"
            onClick={toggleMenu}
          >
            <span className="menu-bar" />
            <span className="menu-bar" />
            <span className="menu-bar" />
          </button>
        </div>
      </div>
      <div className={`mobile-nav${menuOpen ? " open" : ""}`} id="mobile-nav" hidden={!menuOpen}>
        {NAV_LINKS.map((l) => {
          const isActive = current === l.href || current.startsWith(l.href + "/");
          return (
            <Link
              key={l.href}
              href={l.href}
              className={isActive ? "active" : ""}
              aria-current={isActive ? "page" : undefined}
            >
              {l.label}
            </Link>
          );
        })}
      </div>
    </header>
  );
}