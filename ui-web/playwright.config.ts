import { defineConfig } from "@playwright/test";

const baseURL = process.env.UI_WEB_BASE_URL ?? "http://127.0.0.1:3000";

export default defineConfig({
  testDir: "./tests/e2e",
  timeout: 120_000,
  fullyParallel: false,
  workers: 1,
  use: {
    baseURL,
    headless: true,
    launchOptions: {
      slowMo: Number(process.env.PW_SLOW_MO || 0),
    },
  },
  webServer: {
    command: "npm run dev -- --hostname 127.0.0.1 --port 3000",
    url: baseURL,
    reuseExistingServer: true,
    timeout: 120_000,
  },
});
