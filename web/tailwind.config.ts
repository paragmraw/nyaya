import type { Config } from "tailwindcss";

// Tailwind config that mirrors the design tokens from globals.css.
// All colors reference the CSS custom properties so utilities emit
// `var(--accent)` etc., keeping the single source of truth in globals.css.
const config: Config = {
  content: [
    "./src/app/**/*.{ts,tsx}",
    "./src/components/**/*.{ts,tsx}",
    "./src/lib/**/*.{ts,tsx}",
  ],
  theme: {
    extend: {
      colors: {
        bg: "var(--bg)",
        surface: "var(--surface)",
        "surface-2": "var(--surface-2)",
        fg: "var(--fg)",
        muted: "var(--muted)",
        border: "var(--border)",
        accent: "var(--accent)",
        "accent-soft": "var(--accent-soft)",
        "accent-tint": "var(--accent-tint)",
      },
      fontFamily: {
        display: ["var(--font-display)"],
        body: ["var(--font-body)"],
        mono: ["var(--font-mono)"],
      },
      fontSize: {
        h1: "var(--fs-h1)",
        h2: ["var(--fs-h2)", { lineHeight: "1.22", letterSpacing: "-0.018em" }],
        h3: "var(--fs-h3)",
        lead: ["var(--fs-lead)", { lineHeight: "1.5" }],
        body: "var(--fs-body)",
        meta: "var(--fs-meta)",
      },
      spacing: {
        "gap-xs": "var(--gap-xs)",
        "gap-sm": "var(--gap-sm)",
        "gap-md": "var(--gap-md)",
        "gap-lg": "var(--gap-lg)",
        "gap-xl": "var(--gap-xl)",
      },
      borderRadius: {
        DEFAULT: "var(--radius)",
        lg: "var(--radius-lg)",
        xl: "var(--radius-xl)",
      },
      boxShadow: {
        sm: "var(--shadow-sm)",
        md: "var(--shadow-md)",
      },
    },
  },
  plugins: [],
};

export default config;