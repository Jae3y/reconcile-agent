import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  // The repo root CLAUDE.md is the single source of truth; don't generate a
  // second one inside web/ that could drift from it.
  agentRules: false,
};

export default nextConfig;
