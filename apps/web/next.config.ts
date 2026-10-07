import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // `next dev` writes an AGENTS.md when run by a coding agent; our agent rules
  // live in the repo-root CLAUDE.md.
  agentRules: false,
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
