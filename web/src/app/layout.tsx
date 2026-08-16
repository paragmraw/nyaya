import type { Metadata } from "next";
import { Space_Grotesk, Inter, JetBrains_Mono } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import Topnav from "@/components/Topnav";
import Footer from "@/components/Footer";
import RouteBodyClass from "@/components/RouteBodyClass";
import { WebVitals } from "@/lib/analytics";

const spaceGrotesk = Space_Grotesk({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-space-grotesk",
  display: "swap",
  preload: true,
});
const inter = Inter({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700"],
  variable: "--font-inter",
  display: "swap",
  preload: true,
});
const jetbrainsMono = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500", "600"],
  variable: "--font-jetbrains-mono",
  display: "swap",
});

export const metadataBase = new URL("https://nyaya.example.com");

export const metadata: Metadata = {
  title: {
    default: "Nyaya · Conversational AI for Indian law",
    template: "Nyaya · %s",
  },
  description:
    "A retrieval-grounded assistant for practicing lawyers. Every reply traces to a numbered article, section, or judgment.",
  icons: { icon: "/favicon.svg" },
  metadataBase,
  openGraph: {
    type: "website",
    locale: "en_IN",
    url: "https://nyaya.example.com",
    siteName: "Nyaya",
    title: "Nyaya · Conversational AI for Indian law",
    description:
      "A retrieval-grounded assistant for practicing lawyers. Every reply traces to a numbered article, section, or judgment.",
    images: [
      { url: "/logo.svg", width: 1200, height: 630, alt: "Nyaya - Indian Law AI Assistant" },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Nyaya · Conversational AI for Indian law",
    description:
      "A retrieval-grounded assistant for practicing lawyers. Every reply traces to a numbered article, section, or judgment.",
    images: ["/logo.svg"],
  },
  robots: { index: true, follow: true },
  other: {
    "theme-color": "#2d5aff",
  },
};

const siteSchema = {
  "@context": "https://schema.org",
  "@graph": [
    {
      "@type": "Organization",
      "@id": "https://nyaya.example.com/#organization",
      name: "Nyaya",
      url: "https://nyaya.example.com",
      logo: "https://nyaya.example.com/logo.svg",
      sameAs: ["https://github.com/paragmraw/nyaya"],
      description:
        "Conversational AI for Indian law - retrieval-grounded legal research assistant.",
    },
    {
      "@type": "WebSite",
      "@id": "https://nyaya.example.com/#website",
      url: "https://nyaya.example.com",
      name: "Nyaya",
      description:
        "A retrieval-grounded assistant for practicing lawyers.",
      publisher: { "@id": "https://nyaya.example.com/#organization" },
      potentialAction: {
        "@type": "SearchAction",
        target: "https://nyaya.example.com/search?q={search_term_string}",
        "query-input": "required name=search_term_string",
      },
    },
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${spaceGrotesk.variable} ${inter.variable} ${jetbrainsMono.variable}`}>
      <head>
        <meta name="theme-color" content="#2d5aff" />
      </head>
      <body>
        <a href="#content" className="skip-link">Skip to main content</a>
        <RouteBodyClass />
        <Topnav />
        {children}
        <Footer />
        <Script
          id="site-schema"
          type="application/ld+json"
          dangerouslySetInnerHTML={{ __html: JSON.stringify(siteSchema) }}
        />
        <WebVitals />
      </body>
    </html>
  );
}