import { defineConfig } from "@playwright/test";
export default defineConfig({
  testDir: "./tests",
  testMatch: "*.e2e.ts",
  workers: 1,
  timeout: 60000,
  use: {
    baseURL: process.env.E2E_BASE_URL || "http://127.0.0.1:8082",
    headless: true,
    viewport: { width: 1440, height: 1100 },
    launchOptions: process.env.CHROMIUM_PATH
      ? {
          executablePath: process.env.CHROMIUM_PATH,
          args: [
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--use-gl=angle",
            "--use-angle=swiftshader",
            "--disable-gpu-sandbox",
          ],
        }
      : {},
  },
  webServer: process.env.E2E_BASE_URL
    ? undefined
    : {
        command:
          (process.env.PYTHON_BIN || "../.venv/bin/python") +
          " ../hni_accounting_system.py",
        env: { HNI_DB_PATH: "/tmp/goldilocks-e2e-" + process.pid + ".sqlite" },
        url: "http://127.0.0.1:8082/api/health",
        reuseExistingServer: false,
        timeout: 30000,
      },
  reporter: [["list"]],
  outputDir: "test-results",
});
