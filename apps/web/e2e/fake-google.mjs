// Stand-in for Google's OpenID Connect endpoints in E2E runs (never in production: api-commerce
// refuses non-Google GOOGLE_OIDC_* when ENVIRONMENT=production). Node only, no dependencies.
//
//   GET  /o/oauth2/v2/auth   consent is automatic: 302 to redirect_uri?code&state
//   POST /token              checks the PKCE verifier (S256) and returns an RS256 id_token
//   GET  /certs              the public key set
//   POST /__e2e/identity     {sub, email, name} → identity of the next sign-in (default below)
import { createHash, createSign, generateKeyPairSync, randomBytes } from "node:crypto";
import { createServer } from "node:http";

const PORT = Number(process.env.E2E_GOOGLE_PORT ?? 8792);
const ISSUER = `http://127.0.0.1:${PORT}`;
const CLIENT_ID = process.env.E2E_GOOGLE_CLIENT_ID ?? "e2e-client";
const KID = "e2e-key";
const { privateKey, publicKey } = generateKeyPairSync("rsa", { modulusLength: 2048 });
const JWKS = { keys: [{ ...publicKey.export({ format: "jwk" }), kid: KID, alg: "RS256", use: "sig" }] };

const DEFAULT_IDENTITY = { sub: "e2e-customer-1", email: "cliente.e2e@example.com", name: "Cliente E2E" };
let nextIdentity = DEFAULT_IDENTITY;
const codes = new Map(); // code -> { challenge, nonce, identity, expiresAt }

const b64url = (value) => Buffer.from(value).toString("base64url");

function sign(claims) {
  const header = b64url(JSON.stringify({ alg: "RS256", typ: "JWT", kid: KID }));
  const payload = b64url(JSON.stringify(claims));
  const signature = createSign("RSA-SHA256").update(`${header}.${payload}`).sign(privateKey, "base64url");
  return `${header}.${payload}.${signature}`;
}

function send(res, status, body, headers = {}) {
  res.writeHead(status, { "Content-Type": "application/json", ...headers });
  res.end(body === undefined ? "" : JSON.stringify(body));
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  return Buffer.concat(chunks).toString("utf8");
}

createServer(async (req, res) => {
  const url = new URL(req.url ?? "/", `http://${req.headers.host}`);

  if (req.method === "GET" && url.pathname === "/healthz") return send(res, 200, { ok: true });

  if (req.method === "GET" && url.pathname === "/certs") {
    return send(res, 200, JWKS, { "Cache-Control": "public, max-age=600" });
  }

  if (req.method === "POST" && url.pathname === "/__e2e/identity") {
    nextIdentity = { ...DEFAULT_IDENTITY, ...JSON.parse((await readBody(req)) || "{}") };
    return send(res, 204);
  }

  if (req.method === "GET" && url.pathname === "/o/oauth2/v2/auth") {
    const p = url.searchParams;
    if (p.get("client_id") !== CLIENT_ID || p.get("code_challenge_method") !== "S256" || !p.get("state")) {
      return send(res, 400, { error: "invalid_request" });
    }
    const code = randomBytes(24).toString("base64url");
    codes.set(code, {
      challenge: p.get("code_challenge"),
      nonce: p.get("nonce"),
      identity: nextIdentity,
      expiresAt: Date.now() + 60_000,
    });
    nextIdentity = DEFAULT_IDENTITY;
    const back = new URL(p.get("redirect_uri"));
    back.searchParams.set("code", code);
    back.searchParams.set("state", p.get("state"));
    return send(res, 302, undefined, { Location: back.toString() });
  }

  if (req.method === "POST" && url.pathname === "/token") {
    const form = new URLSearchParams(await readBody(req));
    const saved = codes.get(form.get("code") ?? "");
    codes.delete(form.get("code") ?? "");
    const verifier = form.get("code_verifier") ?? "";
    const challenge = createHash("sha256").update(verifier).digest("base64url");
    if (!saved || saved.expiresAt < Date.now() || challenge !== saved.challenge || form.get("client_id") !== CLIENT_ID) {
      return send(res, 400, { error: "invalid_grant" });
    }
    const now = Math.floor(Date.now() / 1000);
    const idToken = sign({
      iss: ISSUER,
      aud: CLIENT_ID,
      sub: saved.identity.sub,
      email: saved.identity.email,
      email_verified: true,
      name: saved.identity.name,
      nonce: saved.nonce,
      iat: now,
      exp: now + 600,
    });
    return send(res, 200, { id_token: idToken, token_type: "Bearer", expires_in: 3600 });
  }

  return send(res, 404, { error: "not_found" });
}).listen(PORT, "127.0.0.1", () => {
  console.log(`fake google on ${ISSUER}`);
});
