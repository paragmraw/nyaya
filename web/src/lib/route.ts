// Route-aware body class, applied PRE-PAINT (plan Task 10 item 5).
//
// The home page locks the viewport (body.home: overflow hidden, flex column,
// definite height) while info pages scroll normally. The class must be on
// <body> before first paint or the home layout visibly reflows (scrollbars
// flash, the frame jumps) when React's first effect ran — previously the
// RouteBodyClass client component applied it post-paint on mount.
//
// This script is inlined as the FIRST child of <body> in layout.tsx, so it
// executes synchronously during HTML parsing, before anything renders. It
// reuses the mechanism/pattern of the theme prepaint script (lib/theme.ts):
// a tiny inline IIFE, no dependencies, safe under blocked JS (it just doesn't
// run — React's sync component still applies the class as a fallback).
//
// Client-side navigations (next/link) do not reload the document, so the
// script cannot fire again; RouteBodyClass keeps syncing the class from
// usePathname() for those (its mount-time application is a no-op once this
// script has run). One class source of truth, two application points chosen
// by when the route becomes known.
export function routePrepaintScript(): string {
  // Template deliberately avoids `${}` interpolation so the script stays one
  // literal string (Next inlines it verbatim into the HTML body).
  return [
    "(function(){",
    "var p=location.pathname;",
    "var h=(p==='/'||p==='');",
    "var b=document.body;",
    "if(!b)return;",
    "b.classList.toggle('home',h);",
    "b.classList.toggle('info',!h);",
    "})();",
  ].join("");
}
