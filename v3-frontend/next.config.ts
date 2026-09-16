import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // Surface the Vercel deployment commit to the browser so users can
  // include "build: c6e2d21" in bug reports. Falls back to "local" in
  // dev so the footer always renders something.
  env: {
    NEXT_PUBLIC_GIT_SHA:
      (process.env.VERCEL_GIT_COMMIT_SHA?.slice(0, 7)) || "local",
  },
  async headers() {
    return [
      {
        source: "/(.*)",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "X-XSS-Protection", value: "1; mode=block" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
        ],
      },
    ];
  },
};

export default nextConfig;
