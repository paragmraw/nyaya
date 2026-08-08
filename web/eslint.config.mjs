import next from "eslint-config-next";
import tsparser from "@typescript-eslint/parser";
import tseslint from "@typescript-eslint/eslint-plugin";
import reactHooks from "eslint-plugin-react-hooks";
import jsxA11y from "eslint-plugin-jsx-a11y";

// Flat-config ESLint for the Next.js SPA (ESLint 9 / eslint-config-next 16).
//
// eslint-config-next already registers the react-hooks, jsx-a11y, and
// @typescript-eslint plugins globally, so we only need to layer on the
// recommended *rules*. We re-declare the @typescript-eslint plugin (with the
// parser) in our own block so its rules resolve for all matched files; the
// react-hooks and jsx-a11y plugins are already available from the next
// config.
const config = [
  ...next,
  {
    ignores: ["node_modules/**", ".next/**", "out/**", "next-env.d.ts"],
  },
  {
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      parser: tsparser,
      parserOptions: {
        ecmaFeatures: { jsx: true },
      },
    },
    rules: {
      // TypeScript ESLint recommended rules.
      ...tseslint.configs.recommended.rules,
      // React hooks: rules-of-hooks + exhaustive-deps.
      ...reactHooks.configs.recommended.rules,
      "react-hooks/exhaustive-deps": "warn",
      // jsx-a11y recommended accessibility rules.
      ...jsxA11y.flatConfigs.recommended.rules,
    },
  },
];

export default config;