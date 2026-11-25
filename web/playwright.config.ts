import { defineConfig, devices } from "@playwright/test";

/**
 * E2E runs against the real stack (`docker compose up`), reset to the seed first (`make e2e`).
 * Clock: the API's running clock starts at APP_NOW (2026-11-11T15:00Z) when the container boots, so
 * "today" is the seed's day; assertions only use relative time (the timer, section changes).
 * A trace is kept for every failure (test-results/…/trace.zip — open with `pnpm exec playwright show-trace`).
 */
export default defineConfig({
  testDir: "./e2e",
  fullyParallel: false, // one team, one live meeting: tests share it, so run in order
  workers: 1,
  retries: 0,
  reporter: [["list"], ["html", { open: "never" }], ["junit", { outputFile: "playwright-junit.xml" }]],
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://localhost:3000",
    trace: "retain-on-failure",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [{ name: "chromium", use: { ...devices["Desktop Chrome"], viewport: { width: 1280, height: 860 } } }],
});
