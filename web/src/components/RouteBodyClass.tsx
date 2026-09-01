"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";

// Route-aware body class so the home page locks the viewport (overflow:
// hidden, flex column) while info pages scroll normally. <body> must be the
// flex container directly (a div wrapper would break the layout).
//
// The INITIAL class is applied pre-paint by the inline routePrepaintScript()
// (lib/route.ts) inlined at the top of <body> — this component only keeps the
// class in sync across client-side navigations (next/link does not reload the
// document, so the prepaint script cannot fire again). Its mount-time
// application is a no-op when the prepaint script already set the class
// (classList.toggle with the same state mutates nothing).
export default function RouteBodyClass() {
  const pathname = usePathname() ?? "/";
  const isHome = pathname === "/" || pathname === "";
  useEffect(() => {
    if (typeof document !== "undefined") {
      // classList.toggle so other classes (e.g. theme) aren't clobbered.
      document.body.classList.toggle("home", isHome);
      document.body.classList.toggle("info", !isHome);
    }
  }, [isHome]);
  return null;
}