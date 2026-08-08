"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

const NAV_LINKS = [
  { href: "/corpus", label: "Corpus" },
  { href: "/citations", label: "Citations" },
  { href: "/architecture", label: "Architecture" },
];

function renderNavLink(link: { href: string; label: string }, current: string) {
  const isActive = current === link.href || current.startsWith(link.href + "/");
  return (
    <Link
      key={link.href}
      href={link.href}
      className={isActive ? "active" : ""}
      aria-current={isActive ? "page" : undefined}
    >
      {link.label}
    </Link>
  );
}

export default function Topnav() {
  const pathname = usePathname();
  const current = (pathname ?? "/").replace(/\/$/, "") || "/";
  const isHome = current === "/";
  const [menuOpen, setMenuOpen] = useState(false);

  // Close the mobile menu on route change.
  const prevPath = useRef(pathname);
  useEffect(() => {
    if (prevPath.current !== pathname) {
      prevPath.current = pathname;
      setMenuOpen(false);
    }
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
          {NAV_LINKS.map((l) => renderNavLink(l, current))}
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
        {NAV_LINKS.map((l) => renderNavLink(l, current))}
      </div>
    </header>
  );
}