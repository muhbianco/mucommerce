import { defineConfig } from "@playwright/test";

/**
 * E2E against the real web + api-commerce, with no Docker:
 *   - api-commerce from `../api-commerce/tests/e2e/server.py` (SQLite, seeded; E2E_PYTHON picks
 *     the interpreter, e.g. the venv's python);
 *   - the MuhBianco login from `fake-accounts.mjs`;
 *   - the web with `next dev` (E2E_WEB_COMMAND to use `next start` after a build in CI).
 *
 * Hosts are `*.localhost`: Chromium resolves them to 127.0.0.1 and treats them as secure, so the
 * `__Host-` session cookies behave as in production. Locally the installed Edge is used
 * (E2E_BROWSER_CHANNEL=msedge, no browser download); CI uses Playwright's own Chromium.
 *
 *   pnpm e2e                                   # from apps/web
 */
const WEB_PORT = Number(process.env.E2E_WEB_PORT ?? 3100);
const API_PORT = 8791;
const ACCOUNTS_PORT = 8790;
const GOOGLE_PORT = 8792;
const N8N_PORT = 8793;
const N8N_SECRET = "e2e-n8n-secret-0123456789";
const WEB_ENV = {
  COMMERCE_API_INTERNAL_URL: `http://127.0.0.1:${API_PORT}`,
  INTERNAL_TOKEN_WEB: "e2e-web-token",
  PANEL_HOST: "painel.localhost",
  PLATFORM_BASE_DOMAIN: "loja.localhost",
  MUHBIANCO_ACCOUNTS_URL: `http://127.0.0.1:${ACCOUNTS_PORT}`,
  NEXT_TELEMETRY_DISABLED: "1",
  // The fetch cache persists in .next across runs; each run reseeds the API with new ids.
  STOREFRONT_REVALIDATE_SECONDS: "0",
};

export default defineConfig({
  testDir: ".",
  testMatch: /.*\.spec\.ts$/,
  fullyParallel: false,
  workers: 1, // one seeded database shared by every spec
  retries: process.env.CI ? 1 : 0,
  timeout: 60_000,
  expect: { timeout: 15_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  use: {
    channel: process.env.E2E_BROWSER_CHANNEL || undefined,
    trace: "retain-on-failure",
    navigationTimeout: 45_000, // `next dev` compiles each route on first hit
  },
  webServer: [
    {
      command: "node fake-google.mjs",
      url: `http://127.0.0.1:${GOOGLE_PORT}/healthz`,
      env: { E2E_GOOGLE_PORT: String(GOOGLE_PORT), E2E_GOOGLE_CLIENT_ID: "e2e-client" },
      reuseExistingServer: false,
    },
    {
      command: "node fake-n8n.mjs",
      url: `http://127.0.0.1:${N8N_PORT}/healthz`,
      env: { E2E_N8N_PORT: String(N8N_PORT), E2E_N8N_SECRET: N8N_SECRET },
      reuseExistingServer: false,
    },
    {
      command: "node fake-accounts.mjs",
      url: `http://127.0.0.1:${ACCOUNTS_PORT}/healthz`,
      env: { E2E_ACCOUNTS_PORT: String(ACCOUNTS_PORT), E2E_PANEL_URL: `http://painel.localhost:${WEB_PORT}` },
      reuseExistingServer: false,
    },
    {
      command: `${process.env.E2E_PYTHON ?? "python"} tests/e2e/server.py`,
      cwd: "../../api-commerce",
      url: `http://127.0.0.1:${API_PORT}/healthz`,
      env: {
        E2E_API_PORT: String(API_PORT),
        E2E_GOOGLE_PORT: String(GOOGLE_PORT),
        E2E_WEB_PORT: String(WEB_PORT),
        MUHBIANCO_ACCOUNTS_INTERNAL_URL: `http://127.0.0.1:${ACCOUNTS_PORT}`,
        NOTIFY_N8N_URL: `http://127.0.0.1:${N8N_PORT}/webhook/commerce-email`,
        NOTIFY_N8N_SECRET: N8N_SECRET,
      },
      timeout: 60_000,
      reuseExistingServer: false,
    },
    {
      command: process.env.E2E_WEB_COMMAND ?? `pnpm exec next dev --port ${WEB_PORT}`,
      cwd: "..",
      url: `http://127.0.0.1:${WEB_PORT}/healthz`,
      env: WEB_ENV,
      timeout: 180_000,
      reuseExistingServer: false,
    },
  ],
});

export const STORE = `http://loja.localhost:${WEB_PORT}`;
export const CLOSED_STORE = `http://fechada.loja.localhost:${WEB_PORT}`;
export const PANEL = `http://painel.localhost:${WEB_PORT}`;
export const FAKE_GOOGLE = `http://127.0.0.1:${GOOGLE_PORT}`;
export const API = `http://127.0.0.1:${API_PORT}`;
export const FAKE_N8N = `http://127.0.0.1:${N8N_PORT}`;
