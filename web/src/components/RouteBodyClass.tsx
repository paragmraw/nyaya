"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";

// Route-aware body class so the home page locks the viewport (overflow:
// hidden, flex column) while info pages scroll normally. <body> must be the
// flex container directly (a div wrapper would break the layout).
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