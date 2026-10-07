import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `next dev` writes an AGENTS.md when run by a coding agent; our agent rules
  // live in the repo-root CLAUDE.md.
  agentRules: false,
  // Self-contained server bundle for the Docker image (apps/web/Dockerfile).
  output: "standalone",
  cacheComponents: true,
  // sharp is excluded (see pnpm-workspace.yaml), so serve images as they are.
  images: { unoptimized: true },
  turbopack: {
    rules: {
      "*.css": {
        loaders: ["@tailwindcss/turbopack"],
        as: "*.css",
      },
    },
  },
};

export default nextConfig;
