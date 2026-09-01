import type { Metadata, Viewport } from "next";
import { Space_Grotesk, Inter, JetBrains_Mono } from "next/font/google";
import Script from "next/script";
import "./globals.css";
import Topnav from "@/components/Topnav";
import Footer from "@/components/Footer";
import RouteBodyClass from "@/components/RouteBodyClass";
import { WebVitals } from "@/lib/analytics";
import { siteSchema } from "@/lib/schema";
import { SITE, OG_IMAGE } from "@/lib/site";
import { themePrepaintScript } from "@/lib/theme";
import { routePrepaintScript } from "@/lib/route";

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
  display: "fallback",
});

export const metadataBase = new URL(SITE);

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
    url: SITE,
    siteName: "Nyaya",
    title: "Nyaya · Conversational AI for Indian law",
    description:
      "A retrieval-grounded assistant for practicing lawyers. Every reply traces to a numbered article, section, or judgment.",
    images: [
      { url: OG_IMAGE, width: 1200, height: 630, alt: "Nyaya - Indian Law AI Assistant" },
    ],
  },
  twitter: {
    card: "summary_large_image",
    title: "Nyaya · Conversational AI for Indian law",
    description:
      "A retrieval-grounded assistant for practicing lawyers. Every reply traces to a numbered article, section, or judgment.",
    images: [OG_IMAGE],
  },
  robots: { index: true, follow: true },
};

// Browser-chrome tint per scheme. (Previously emitted twice — once via
// `metadata.other` and once as a hand-written <meta> — and never adapted to
// dark mode.)
export const viewport: Viewport = {
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#2d5aff" },
    { media: "(prefers-color-scheme: dark)", color: "#161d33" },
  ],
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    // suppressHydrationWarning: the pre-paint script below sets data-theme
    // on <html> before hydration, which React does not own.
    <html
      lang="en"
      suppressHydrationWarning
      className={`${spaceGrotesk.variable} ${inter.variable} ${jetbrainsMono.variable}`}
    >
      <head>
        {/* Pre-paint theme: stored choice wins, else OS preference — no FOUC. */}
        <script dangerouslySetInnerHTML={{ __html: themePrepaintScript() }} />
      </head>
      <body>
        {/* Pre-paint route class: body.home (viewport lock) / body.info must
            be on <body> before first paint. Runs synchronously during parsing;
            RouteBodyClass below keeps it in sync across client-side navs. */}
        <script dangerouslySetInnerHTML={{ __html: routePrepaintScript() }} />
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