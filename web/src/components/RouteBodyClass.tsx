"use client";

import { useEffect } from "react";
import { usePathname } from "next/navigation";

// Route-aware body class so the home page can lock the viewport
// (overflow: hidden; display: flex) while info pages scroll normally.
// Sets `document.body.className` directly so <body> is the flex container
// (a div wrapper would break the flex layout: topnav + frame must be
// direct children of the flex-column body).
export default function RouteBodyClass() {
  const pathname = usePathname() ?? "/";
  const isHome = pathname === "/" || pathname === "";
  useEffect(() => {
    if (typeof document !== "undefined") {
      document.body.className = isHome ? "home" : "info";
    }
  }, [isHome]);
  return null;
}