"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import Script from "next/script";

interface BreadcrumbItem {
  label: string;
  href: string;
}

const ROUTE_MAP: Record<string, BreadcrumbItem> = {
  "/": { label: "Home", href: "/" },
  "/corpus/": { label: "Corpus", href: "/corpus/" },
  "/citations/": { label: "Citations", href: "/citations/" },
  "/architecture/": { label: "Architecture", href: "/architecture/" },
};

export default function Breadcrumb() {
  const pathname = usePathname();
  const current = (pathname ?? "/").replace(/\/$/, "") || "/";
  const isHome = current === "/";

  if (isHome) return null;

  const items: BreadcrumbItem[] = [
    ROUTE_MAP["/"],
    ROUTE_MAP[`${current}/`] || { label: current.split("/").pop() || "", href: current },
  ].filter(Boolean);

  const breadcrumbSchema = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: items.map((item, index) => ({
      "@type": "ListItem",
      position: index + 1,
      name: item.label,
      item: `https://nyaya.example.com${item.href}`,
    })),
  };

  return (
    <>
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <ol className="breadcrumb-list">
          {items.map((item, index) => (
            <li key={item.href} className="breadcrumb-item">
              {index > 0 && <span className="breadcrumb-sep" aria-hidden="true">/</span>}
              {index === items.length - 1 ? (
                <span className="breadcrumb-current" aria-current="page">{item.label}</span>
              ) : (
                <Link href={item.href} className="breadcrumb-link">{item.label}</Link>
              )}
            </li>
          ))}
        </ol>
      </nav>
      <Script
        id="breadcrumb-schema"
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: JSON.stringify(breadcrumbSchema) }}
        strategy="lazyOnload"
      />
    </>
  );
}