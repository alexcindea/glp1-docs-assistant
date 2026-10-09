import { defineConfig, devices } from "@playwright/test";

/**
 * End-to-end tests against a running stack: `docker compose up` in CI, or the
 * native dev servers. E2E_BASE_URL says where the web app is.
 *
 *   pnpm exec playwright test                     # against http://localhost:3000
 *   $env:MEASURE_UI = "1"; pnpm exec playwright test measure   # timings, real model
 */
export default defineConfig({
  testDir: "e2e",
  timeout: 240_000, // a fresh worker reads the PDF first
  expect: { timeout: 30_000 },
  retries: process.env.CI ? 1 : 0,
  workers: 1,
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"] } }],
});
