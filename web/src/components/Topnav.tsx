"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

const NAV_LINKS = [
  { href: "/corpus", label: "Corpus" },
  { href: "/citations", label: "Citations" },
  { href: "/architecture", label: "Architecture" },
];

export default function Topnav() {
  const pathname = usePathname();
  const current = (pathname ?? "/").replace(/\/$/, "") || "/";
  const isHome = current === "/";

  return (
    <header className={`topnav${isHome ? "" : " topnav-sticky"}`}>
      <div className="container topnav-inner">
        <Link href="/" className="logo" aria-label="Nyaya">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src="/logo.png" alt="Nyaya" />
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
          <span className="meta">v0.9 · beta</span>
        </div>
      </div>
    </header>
  );
}