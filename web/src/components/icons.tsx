// Hand-inlined Material Icons (was @mui/icons-material via @mui/material +
// @emotion). These were the only two glyphs used; inlining them drops the
// entire MUI/Emotion dependency tree from the bundle. Path data is verbatim
// from the MUI packages so the rendered glyphs are pixel-identical.
// The former MUI usage was <Icon fontSize="small"> → 20×20 px; the fixed
// width/height below preserves that footprint everywhere the icons render.

export const BalanceIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true" focusable="false">
    <path d="M13 7.83c.85-.3 1.53-.98 1.83-1.83H18l-3 7c0 1.66 1.57 3 3.5 3s3.5-1.34 3.5-3l-3-7h2V4h-6.17c-.41-1.17-1.52-2-2.83-2s-2.42.83-2.83 2H3v2h2l-3 7c0 1.66 1.57 3 3.5 3S9 14.66 9 13L6 6h3.17c.3.85.98 1.53 1.83 1.83V19H2v2h20v-2h-9zM20.37 13h-3.74l1.87-4.36zm-13 0H3.63L5.5 8.64zM12 6c-.55 0-1-.45-1-1s.45-1 1-1 1 .45 1 1-.45 1-1 1" />
  </svg>
);

export const PersonIcon = () => (
  <svg viewBox="0 0 24 24" width="20" height="20" fill="currentColor" aria-hidden="true" focusable="false">
    <path d="M12 12c2.21 0 4-1.79 4-4s-1.79-4-4-4-4 1.79-4 4 1.79 4 4 4m0 2c-2.67 0-8 1.34-8 4v2h16v-2c0-2.66-5.33-4-8-4" />
  </svg>
);
