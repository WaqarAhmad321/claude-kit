import { existsSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

// The e2e logins and the Supabase URL and key come from .env, as they do for the dev server.
if (existsSync(".env")) {
  process.loadEnvFile(".env");
}

const PORT = 3100;
const IN_CI = Boolean(process.env.CI);

export default defineConfig({
  testDir: "./e2e",
  fullyParallel: true,
  forbidOnly: IN_CI,
  retries: IN_CI ? 1 : 0,
  reporter: IN_CI ? [["github"], ["html", { open: "never" }]] : "list",
  use: {
    baseURL: `http://localhost:${PORT}`,
    trace: "retain-on-failure",
  },
  webServer: {
    command: `pnpm exec next dev --port ${PORT}`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !IN_CI,
    timeout: 120_000,
  },
  projects: [
    { name: "setup", testMatch: /.*\.setup\.ts/ },
    { name: "chromium", use: { ...devices["Desktop Chrome"] }, dependencies: ["setup"] },
  ],
});
