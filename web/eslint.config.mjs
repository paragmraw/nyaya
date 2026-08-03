import next from "eslint-config-next";

// Flat-config ESLint for the Next.js SPA (ESLint 9 / eslint-config-next 16).
// The package's default export is already a flat-config array that includes
// the core-web-vitals rules.
const config = [
  ...next,
  {
    ignores: ["node_modules/**", ".next/**", "out/**", "next-env.d.ts"],
  },
];

export default config;