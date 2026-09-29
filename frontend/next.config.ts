import type { NextConfig } from "next";

const backend = (process.env.BACKEND_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const config: NextConfig = {
  distDir: process.env.NEXT_TEST_BUILD === "1" ? ".next-test" : ".next",
  poweredByHeader: false,
  experimental: { proxyClientMaxBodySize: "55mb" },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${backend}/:path*` }];
  },
};
export default config;
