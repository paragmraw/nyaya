"use client";

import Link from "next/link";
import Image from "next/image";
import { usePathname } from "next/navigation";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { applyTheme, currentTheme, THEME_STORAGE_KEY } from "@/lib/theme";

// Subscribe to <html data-theme> so components re-render when the theme
// changes (via the pre-paint script, the toggle, or the OS listener).
function subscribeToTheme(onChange: () => void): () => void {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, {
    attributes: true,
    attributeFilter: ["data-theme"],
  });
  window.addEventListener("storage", onChange);
  return () => {
    observer.disconnect();
    window.removeEventListener("storage", onChange);
  };
}

// Sun / moon glyphs for the theme toggle. Both icons are always rendered and
// visibility is driven purely by CSS ([data-theme] on <html>), so the markup
// matches the server HTML exactly — no hydration flicker. The moon shows in
// light mode, the sun in dark mode (the icon names the current scheme).
function ThemeIcon({ variant }: { variant: "sun" | "moon" }) {
  return variant === "sun" ? (
    <svg className="theme-icon theme-icon-sun" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <circle cx="12" cy="12" r="4" />
      <path d="M12 2v2m0 16v2M4.93 4.93l1.41 1.41m11.32 11.32 1.41 1.41M2 12h2m16 0h2M4.93 19.07l1.41-1.41M17.66 6.34l1.41-1.41" />
    </svg>
  ) : (
    <svg className="theme-icon theme-icon-moon" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false">
      <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
    </svg>
  );
}

const NAV_LINKS = [
  { href: "/corpus/", label: "Corpus" },
  { href: "/citations/", label: "Citations" },
  { href: "/architecture/", label: "Architecture" },
];

function renderNavLink(
  link: { href: string; label: string },
  current: string,
  onNavigate?: () => void,
) {
  const isActive = current === link.href || current.startsWith(link.href);
  return (
    <Link
      key={link.href}
      href={link.href}
      className={isActive ? "active" : ""}
      aria-current={isActive ? "page" : undefined}
      onClick={onNavigate}
    >
      {link.label}
    </Link>
  );
}

export default function Topnav() {
  const pathname = usePathname();
  const current = pathname ?? "/";
  const isHome = current === "/";
  const [menuOpen, setMenuOpen] = useState(false);
  const toggleRef = useRef<HTMLButtonElement>(null);
  const navRef = useRef<HTMLDivElement>(null);

  // Close the mobile menu on route change.
  const prevPath = useRef(pathname);
  useEffect(() => {
    if (prevPath.current !== pathname) {
      prevPath.current = pathname;
      setMenuOpen(false);
    }
  }, [pathname]);

  // Close the mobile menu on Escape + restore focus to the toggle.
  useEffect(() => {
    if (!menuOpen) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        setMenuOpen(false);
        toggleRef.current?.focus();
      }
      // Simple focus trap: Tab cycles within the panel.
      if (e.key === "Tab" && navRef.current) {
        const focusable = navRef.current.querySelectorAll<HTMLAnchorElement>(
          'a[href], button:not([disabled])',
        );
        if (focusable.length === 0) return;
        const first = focusable[0];
        const last = focusable[focusable.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [menuOpen]);

  // Body scroll lock while the menu is open (mobile only).
  useEffect(() => {
    if (!menuOpen) return;
    const original = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      document.body.style.overflow = original;
    };
  }, [menuOpen]);

  // Outside-click (on the header chrome, not a link) closes the menu.
  useEffect(() => {
    if (!menuOpen) return;
    const onPointer = (e: PointerEvent) => {
      const target = e.target as Node | null;
      if (!target) return;
      // Ignore clicks on the toggle itself (it handles its own state) and
      // any click inside the nav panel (links close via route change).
      if (toggleRef.current?.contains(target)) return;
      if (navRef.current?.contains(target)) return;
      setMenuOpen(false);
    };
    window.addEventListener("pointerdown", onPointer);
    return () => window.removeEventListener("pointerdown", onPointer);
  }, [menuOpen]);

  // Theme toggle. The effective theme lives on <html data-theme> (set
  // pre-paint; globals.css implements both schemes as token redefinitions).
  // useSyncExternalStore subscribes to that attribute (MutationObserver) —
  // during hydration React reads getServerSnapshot ("light"), then swaps to
  // the real value without a hydration mismatch.
  const theme = useSyncExternalStore(subscribeToTheme, currentTheme, () => "light");

  // Follow live OS changes only while the user has not pinned a choice —
  // once they toggle, their stored entry wins and this becomes a no-op.
  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => {
      try {
        if (localStorage.getItem(THEME_STORAGE_KEY)) return;
      } catch {
        /* storage blocked — treat as unpinned */
      }
      applyTheme(e.matches ? "dark" : "light", false);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const toggleTheme = useCallback(() => {
    applyTheme(theme === "dark" ? "light" : "dark", true);
  }, [theme]);

  const toggleMenu = useCallback(() => setMenuOpen((v) => !v), []);
  const closeMenu = useCallback(() => setMenuOpen(false), []);

  return (
    <header className={`topnav${isHome ? "" : " topnav-sticky"}`}>
      <div className="container topnav-inner">
        <Link href="/" className="logo" aria-label="Nyaya">
          <Image
            src="/logo.svg"
            alt="Nyaya"
            width={30}
            height={30}
            priority
            sizes="30px"
          />
        </Link>
        <nav>
          {NAV_LINKS.map((l) => renderNavLink(l, current))}
        </nav>
        <div className="nav-right">
          <span className="meta">v0.2.0 · alpha</span>
          <a
            className="github-link"
            href="https://github.com/paragmraw/nyaya"
            target="_blank"
            rel="noopener noreferrer"
            aria-label="Nyaya on GitHub"
          >
            <svg
              width="20"
              height="20"
              viewBox="0 0 16 16"
              fill="currentColor"
              aria-hidden="true"
              focusable="false"
            >
              <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82.64-.18 1.32-.27 2-.27.68 0 1.36.09 2 .27 1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.012 8.012 0 0016 8c0-4.42-3.58-8-8-8z" />
            </svg>
          </a>
          <button
            type="button"
            className="theme-toggle"
            onClick={toggleTheme}
            aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
            title={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
          >
            <ThemeIcon variant="sun" />
            <ThemeIcon variant="moon" />
          </button>
          <button
            type="button"
            ref={toggleRef}
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
      <div
        className={`mobile-nav${menuOpen ? " open" : ""}`}
        id="mobile-nav"
        ref={navRef}
        inert={!menuOpen || undefined}
        aria-hidden={!menuOpen}
      >
        <div className="mobile-nav-scroll">
          {NAV_LINKS.map((l) => renderNavLink(l, current, closeMenu))}
        </div>
      </div>
    </header>
  );
}