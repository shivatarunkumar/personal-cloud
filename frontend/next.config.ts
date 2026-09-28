import type { NextConfig } from "next";

// The browser always calls same-origin /api/*; Next proxies it to FastAPI. That keeps the
// session cookies first-party and needs no CORS. File bytes for viewing and downloading
// stream through this proxy too; uploads go straight from the browser to the bucket.
const apiUrl = process.env.API_INTERNAL_URL ?? "http://localhost:8002";

// The dev server only serves its scripts to localhost unless told otherwise, so a page
// opened at WEB_BASE_URL (e.g. http://mycloud.com:3002) would load without JavaScript.
function devOrigins(): string[] {
  const hosts = [process.env.WEB_BASE_URL, ...(process.env.EXTRA_ORIGINS ?? "").split(",")]
    .map((url) => {
      try {
        return new URL((url ?? "").trim()).hostname;
      } catch {
        return "";
      }
    })
    .filter((host) => host && host !== "localhost");
  return [...new Set(hosts)];
}

const nextConfig: NextConfig = {
  allowedDevOrigins: devOrigins(),
  experimental: {
    // a long video or a big download streams for a while: don't cut the proxy off
    proxyTimeout: 6 * 60 * 60 * 1000,
  },
  async rewrites() {
    return [{ source: "/api/:path*", destination: `${apiUrl}/api/:path*` }];
  },
};

export default nextConfig;
