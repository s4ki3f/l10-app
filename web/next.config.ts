import type { NextConfig } from "next";

// The browser only talks to this origin; /api/* is forwarded to FastAPI. Same origin means the
// session cookie needs no CORS, and SSE streams through the same proxy.
const API_URL = process.env.API_URL ?? "http://localhost:8000";

const nextConfig: NextConfig = {
  output: "standalone",
  compress: false, // never buffer/gzip the SSE stream
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${API_URL}/:path*` }];
  },
};

export default nextConfig;
