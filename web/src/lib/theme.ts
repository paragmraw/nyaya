// Dark-mode mechanism (plan user-decision 9): the system palette comes from
// `@media (prefers-color-scheme: dark)` in globals.css; an explicit user
// choice is expressed as a `data-theme="light"|"dark"` attribute on
// <html>, set pre-paint by THEME_PREPAINT_SCRIPT (no FOUC) and persisted in
// localStorage under THEME_STORAGE_KEY.
//
// Everything here is plain CSS-variable-compatible CSS — the tokens live on
// `:root` / `[data-theme]` selectors with no Tailwind-dependent syntax, so
// P5's Tailwind/preflight removal cannot break it (handoff noted in the
// task-9 report).

export const THEME_STORAGE_KEY = "nyaya-theme";

export type Theme = "light" | "dark";

/**
 * Pick the effective theme. An explicit (valid) stored choice wins over the
 * OS setting; everything else falls back to the system preference.
 */
export function resolveTheme(
  stored: string | null | undefined,
  systemDark: boolean,
): Theme {
  if (stored === "light" || stored === "dark") return stored;
  return systemDark ? "dark" : "light";
}

/**
 * Runs inline in <head>, before first paint, so the initial scheme is
 * correct with zero flash: stored choice wins, otherwise the OS setting.
 * Wrapped in try/catch — blocked storage or odd embeds must never break
 * page load. Also sets `data-theme` on <html>, which globals.css reads.
 */
export function themePrepaintScript(): string {
  // Template deliberately avoids `${}` interpolation so the script stays one
  // literal string (Next inlines it verbatim into the HTML head).
  return [
    "(function(){",
    `var k=${JSON.stringify(THEME_STORAGE_KEY)};`,
    "var m=window.matchMedia?window.matchMedia('(prefers-color-scheme: dark)').matches:false;",
    "var t;",
    "try{var s=localStorage.getItem(k);t=(s==='light'||s==='dark')?s:(m?'dark':'light');}",
    "catch(e){t=m?'dark':'light';}",
    "var d=document.documentElement;",
    "d.setAttribute('data-theme',t);",
    "d.style.colorScheme=t;",
    "})();",
  ].join("");
}

/**
 * Apply a (newly toggled) theme to the live document. Used by the Topnav
 * toggle; also re-applies on <html> so it works regardless of where React
 * has re-rendered.
 */
export function applyTheme(theme: Theme, persist: boolean): void {
  if (typeof document === "undefined") return;
  const root = document.documentElement;
  root.setAttribute("data-theme", theme);
  root.style.colorScheme = theme;
  if (persist) {
    try {
      localStorage.setItem(THEME_STORAGE_KEY, theme);
    } catch {
      // storage unavailable (private mode, blocked) — theme still applies
      // for this document instance.
    }
  }
}

/** Current effective theme, falling back to the system preference. */
export function currentTheme(): Theme {
  if (typeof document === "undefined") return "light";
  const attr = document.documentElement.getAttribute("data-theme");
  const systemDark =
    typeof window !== "undefined" &&
    !!window.matchMedia &&
    window.matchMedia("(prefers-color-scheme: dark)").matches;
  return resolveTheme(attr, systemDark);
}