// Generates public/og-default.png (1200x630) deterministically.
//
//   npx tsx scripts/generate-og.ts
//
// Why not SVG <text>? sharp's SVG rasteriser resolves fonts through
// fontconfig, which is often blank/absent on CI and Windows — the output
// would silently lose its type. Instead every glyph is drawn from an
// embedded 5x7 bitmap font as SVG <rect> pixels, so the PNG depends on
// nothing outside this file (plus sharp, which ships with Next). Palette is
// the site's light scheme (tokens in src/app/globals.css).

import { mkdirSync } from "node:fs";
import sharp from "sharp";

const W = 1200;
const H = 630;

const PAPER = "#f6f4ef";
const INK = "#161d33";
const ACCENT = "#2d5aff";

// 5x7 bitmap font — one row value per row, 5 bits, MSB = leftmost column.
const FONT: Record<string, number[]> = {
  A: [0x0e, 0x11, 0x11, 0x1f, 0x11, 0x11, 0x11],
  B: [0x1e, 0x11, 0x11, 0x1e, 0x11, 0x11, 0x1e],
  C: [0x0e, 0x11, 0x10, 0x10, 0x10, 0x11, 0x0e],
  D: [0x1e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x1e],
  E: [0x1f, 0x10, 0x10, 0x1e, 0x10, 0x10, 0x1f],
  F: [0x1f, 0x10, 0x10, 0x1e, 0x10, 0x10, 0x10],
  G: [0x0e, 0x11, 0x10, 0x10, 0x13, 0x11, 0x0f],
  H: [0x11, 0x11, 0x11, 0x1f, 0x11, 0x11, 0x11],
  I: [0x0e, 0x04, 0x04, 0x04, 0x04, 0x04, 0x0e],
  K: [0x11, 0x12, 0x14, 0x18, 0x14, 0x12, 0x11],
  L: [0x10, 0x10, 0x10, 0x10, 0x10, 0x10, 0x1f],
  M: [0x11, 0x1b, 0x15, 0x15, 0x11, 0x11, 0x11],
  N: [0x11, 0x19, 0x15, 0x13, 0x11, 0x11, 0x11],
  O: [0x0e, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e],
  P: [0x1e, 0x11, 0x11, 0x1e, 0x10, 0x10, 0x10],
  R: [0x1e, 0x11, 0x11, 0x1e, 0x14, 0x12, 0x11],
  S: [0x0f, 0x10, 0x10, 0x0e, 0x01, 0x01, 0x1e],
  T: [0x1f, 0x04, 0x04, 0x04, 0x04, 0x04, 0x04],
  U: [0x11, 0x11, 0x11, 0x11, 0x11, 0x11, 0x0e],
  V: [0x11, 0x11, 0x11, 0x11, 0x0a, 0x0a, 0x04],
  W: [0x11, 0x11, 0x11, 0x15, 0x15, 0x1b, 0x11],
  Y: [0x11, 0x11, 0x0a, 0x04, 0x04, 0x04, 0x04],
  "0": [0x0e, 0x11, 0x13, 0x15, 0x19, 0x11, 0x0e],
  "1": [0x04, 0x0c, 0x04, 0x04, 0x04, 0x04, 0x0e],
  "2": [0x0e, 0x11, 0x01, 0x06, 0x08, 0x10, 0x1f],
  "4": [0x02, 0x06, 0x0a, 0x12, 0x1f, 0x02, 0x02],
  ".": [0x00, 0x00, 0x00, 0x00, 0x00, 0x0c, 0x0c],
  ",": [0x00, 0x00, 0x00, 0x00, 0x0c, 0x04, 0x08],
  " ": [0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00],
};

/** Emit one line of bitmap-set text as SVG rects. Returns its width in px. */
function textRects(
  text: string,
  x: number,
  y: number,
  scale: number,
  fill: string,
): { rects: string[]; width: number } {
  const rects: string[] = [];
  let cx = x;
  for (const ch of text.toUpperCase()) {
    const glyph = FONT[ch];
    if (!glyph) throw new Error(`missing glyph: ${JSON.stringify(ch)}`);
    for (let row = 0; row < 7; row++) {
      const bits = glyph[row];
      for (let col = 0; col < 5; col++) {
        if (bits & (1 << (4 - col))) {
          rects.push(
            `<rect x="${cx + col * scale}" y="${y + row * scale}" width="${scale}" height="${scale}" fill="${fill}"/>`,
          );
        }
      }
    }
    cx += 5 * scale + 2 * scale; /* letter-spacing */
  }
  return { rects, width: cx - x - 2 * scale };
}

/** Rounded pill chip with bitmap-set label. Returns its width. */
function pill(label: string, x: number, y: number, scale: number, fill: string, stroke: string): { shapes: string[]; width: number } {
  const { rects, width } = textRects(label, 0, 0, scale, fill);
  const padX = 6 * scale;
  const padY = 3 * scale;
  const w = width + 2 * padX;
  const h = 7 * scale + 2 * padY;
  const shapes = [
    `<rect x="${x}" y="${y}" width="${w}" height="${h}" rx="${h / 2}" fill="none" stroke="${stroke}" stroke-width="${Math.max(2, scale)}"/>`,
    `<g transform="translate(${x + padX}, ${y + padY})">${rects.join("")}</g>`,
  ];
  return { shapes, width: w };
}

// Background: paper + faint accent-washed gradient, mirroring the body's
// paper texture. A soft accent band anchors the bottom.
const bg = `
  <rect width="${W}" height="${H}" fill="${PAPER}"/>
  <rect width="${W}" height="${H}" fill="url(#tint)"/>
  <rect x="0" y="${H - 14}" width="${W}" height="14" fill="${ACCENT}" opacity="0.9"/>
  <rect x="0" y="0" width="16" height="${H}" fill="${ACCENT}"/>
  <defs>
    <radialGradient id="tint" cx="18%" cy="8%" r="90%">
      <stop offset="0%" stop-color="${ACCENT}" stop-opacity="0.07"/>
      <stop offset="55%" stop-color="${ACCENT}" stop-opacity="0"/>
      <stop offset="100%" stop-color="${INK}" stop-opacity="0.05"/>
    </radialGradient>
  </defs>`;

// Dot grid, top-right — echoes the site's dotted-paper background.
const dots: string[] = [];
for (let ix = 0; ix < 16; ix++) {
  for (let iy = 0; iy < 6; iy++) {
    dots.push(
      `<circle cx="${760 + ix * 27}" cy="${52 + iy * 27}" r="2.4" fill="${ACCENT}" opacity="0.28"/>`,
    );
  }
}

const eyebrow = textRects("Retrieval", 84, 72, 4, ACCENT);
const wordmark = textRects("Nyaya", 84, 148, 16, INK);
const tagline = textRects("Conversational AI for Indian law", 84, 332, 3, INK);
const urlLine = textRects("nyaya.parag.tech", 84, 548, 3, ACCENT);

const pillDefs = [
  ["Art. 21", 0],
  ["S. 41A, CrPC", 0],
  ["K.S. Puttaswamy v. UOI", 0],
] as const;
let pillX = 84;
const pillY = 424;
const pillScale = 2;
const pillShapes: string[] = [];
for (const [label] of pillDefs) {
  const { shapes, width } = pill(label, pillX, pillY, pillScale, INK, "#9aa4b8");
  pillShapes.push(...shapes);
  pillX += width + 18;
}
const pillsWidth = pillX - 84 - 18;

const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
${bg}
${dots.join("\n")}
${eyebrow.rects.join("\n")}
${wordmark.rects.join("\n")}
${tagline.rects.join("\n")}
${pillShapes.join("\n")}
${urlLine.rects.join("\n")}
</svg>`;

// Sanity checks — fail loudly rather than ship a wrong image.
for (const [name, art] of [["eyebrow", eyebrow], ["wordmark", wordmark], ["tagline", tagline], ["url", urlLine]] as const) {
  if (art.width > W - 168) throw new Error(`${name} overflowing: ${art.width}px`);
}
if (pillX > W - 80) throw new Error(`pills overflowing: ${pillsWidth}px`);

mkdirSync("public", { recursive: true });
const png = await sharp(Buffer.from(svg))
  .png({ compressionLevel: 9 })
  .toFile("public/og-default.png");

if (png.width !== 1200 || png.height !== 630) {
  throw new Error(`wrong dimensions: ${png.width}x${png.height}`);
}
console.log(`public/og-default.png written: ${png.width}x${png.height}, ${png.size} bytes`);