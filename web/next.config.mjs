/** @type {import('next').NextConfig} */
const nextConfig = {
  // Static HTML/CSS/JS export — no Node server at runtime.
  // The Python MCP container serves the built `out/` via Starlette StaticFiles.
  output: "export",

  // No image optimization in static export — serve images as-is.
  images: {
    unoptimized: true,
  },

  // Disable source maps in production to avoid leaking source code structure.
  productionBrowserSourceMaps: false,

  // App Router pages are statically generated at build time. Live data is
  // fetched client-side against the same-origin REST endpoints.
  trailingSlash: true,

  // Dev-only rewrites so the SPA can proxy /api/*, /mcp, and /chat/* to the
  // local uvicorn server during `npm run dev`. Next strips rewrites from the
  // `output: 'export'` production build (a benign warning is emitted), so
  // this only affects local dev.
  async rewrites() {
    return [
      { source: "/api/:path*", destination: "http://localhost:8000/api/:path*" },
      { source: "/mcp", destination: "http://localhost:8000/mcp" },
      { source: "/chat/:path*", destination: "http://localhost:8000/chat/:path*" },
    ];
  },
};

export default nextConfig;