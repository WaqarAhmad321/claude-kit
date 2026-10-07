import { existsSync } from "node:fs";
import { defineConfig, devices } from "@playwright/test";

// The e2e logins and the API's address come from .env, as they do for the dev server.
if (existsSync(".env")) {
  process.loadEnvFile(".env");
}

const PORT = 4173;
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
    command: `pnpm exec vite --port ${PORT} --strictPort`,
    url: `http://localhost:${PORT}`,
    reuseExistingServer: !IN_CI,
  },
  projects: [
    { name: "setup", testMatch: /.*\.setup\.ts/ },
    { name: "chromium", use: { ...devices["Desktop Chrome"] }, dependencies: ["setup"] },
  ],
});
