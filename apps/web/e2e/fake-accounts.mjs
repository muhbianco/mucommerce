// Stand-in for the MuhBianco accounts service (api-agents) in E2E runs. Two endpoints, same
// contract as production (lib/panel/sso.ts, api-commerce app/identity/accounts.py):
//
//   GET  /api/v1/auth/google/login?app=commerce&client_state=S&code_challenge=C
//        → 302 to <panel>/sso/callback?code=<one-time>&state=S   (the "Google login" always succeeds)
//   POST /api/v1/auth/commerce/redeem {code, code_verifier}
//        → 200 {account_id, email, full_name, role, email_verified}  when S256(verifier) == challenge
//
// Codes are single use and expire in 60 s, like the real service. No dependencies: node only.
import { createHash, randomBytes } from "node:crypto";
import { createServer } from "node:http";

const PORT = Number(process.env.E2E_ACCOUNTS_PORT ?? 8790);
const PANEL_URL = process.env.E2E_PANEL_URL ?? "http://painel.localhost:3100";
export const E2E_ACCOUNT = {
  account_id: "00000000-0000-4000-8000-00000000e2e0",
  email: "e2e-admin@muhbianco.test",
  full_name: "Admin E2E",
  role: "admin",
  email_verified: true,
};

const codes = new Map(); // code -> { challenge, expiresAt }

function s256(verifier) {
  return createHash("sha256").update(verifier).digest("base64url");
}

function send(res, status, body, headers = {}) {
  res.writeHead(status, { "Content-Type": "application/json", ...headers });
  res.end(body === undefined ? "" : JSON.stringify(body));
}

async function readJson(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  try {
    return JSON.parse(Buffer.concat(chunks).toString("utf8") || "{}");
  } catch {
    return {};
  }
}

createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://${req.headers.host}`);
  if (req.method === "GET" && url.pathname === "/healthz") return send(res, 200, { ok: true });

  // api-agents: the official number store customers send "CONFIRMAR <código>" to.
  if (req.method === "GET" && url.pathname === "/api/v1/internal/commerce/whatsapp-entry") {
    if (req.headers["x-internal-token"] !== "e2e-agents-token") return send(res, 401, {});
    return send(res, 200, { phone: "5511940000000" });
  }

  if (req.method === "GET" && url.pathname === "/api/v1/auth/google/login") {
    const state = url.searchParams.get("client_state");
    const challenge = url.searchParams.get("code_challenge");
    if (url.searchParams.get("app") !== "commerce" || !state || !challenge) {
      return send(res, 422, { error: { code: "validation_error" } });
    }
    const code = randomBytes(24).toString("base64url");
    codes.set(code, { challenge, expiresAt: Date.now() + 60_000 });
    const target = new URL("/sso/callback", PANEL_URL);
    target.searchParams.set("code", code);
    target.searchParams.set("state", state);
    return send(res, 302, undefined, { Location: target.toString() });
  }

  if (req.method === "POST" && url.pathname === "/api/v1/auth/commerce/redeem") {
    const { code, code_verifier: verifier } = await readJson(req);
    const saved = typeof code === "string" ? codes.get(code) : undefined;
    codes.delete(code);
    if (!saved || saved.expiresAt < Date.now() || typeof verifier !== "string" || s256(verifier) !== saved.challenge) {
      return send(res, 401, { error: { code: "invalid_code" } });
    }
    return send(res, 200, E2E_ACCOUNT);
  }

  return send(res, 404, { error: { code: "not_found" } });
}).listen(PORT, "127.0.0.1", () => {
  console.log(`fake accounts on http://127.0.0.1:${PORT} → ${PANEL_URL}`);
});
