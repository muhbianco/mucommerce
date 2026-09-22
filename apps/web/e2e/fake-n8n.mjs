// The n8n workflow that sends the store's e-mails, for the E2E suite: it checks the signature
// the same way the real workflow must (HMAC-SHA256 over "<timestamp>.<raw body>", 5 minute
// window) and keeps the messages so a spec can read them.
//
//   POST /webhook/commerce-email   the API sends an e-mail here
//   GET  /__e2e/messages?to=…      what was "sent" (newest first)
//   GET  /healthz
import { createHmac, timingSafeEqual } from "node:crypto";
import { createServer } from "node:http";

const PORT = Number(process.env.E2E_N8N_PORT ?? 8793);
const SECRET = process.env.E2E_N8N_SECRET ?? "";
const WINDOW_SECONDS = 300;
const messages = [];

function verify(rawBody, header) {
  const parts = Object.fromEntries(
    String(header ?? "")
      .split(",")
      .map((piece) => piece.split("=").map((s) => s.trim())),
  );
  if (!parts.t || !parts.v1) return "missing signature";
  if (Math.abs(Date.now() / 1000 - Number(parts.t)) > WINDOW_SECONDS) return "stale signature";
  const expected = createHmac("sha256", SECRET).update(`${parts.t}.`).update(rawBody).digest("hex");
  const sent = Buffer.from(parts.v1, "utf8");
  const mine = Buffer.from(expected, "utf8");
  if (sent.length !== mine.length || !timingSafeEqual(sent, mine)) return "bad signature";
  return null;
}

const server = createServer((request, response) => {
  const url = new URL(request.url, `http://127.0.0.1:${PORT}`);
  const json = (status, body) => {
    response.writeHead(status, { "content-type": "application/json" });
    response.end(JSON.stringify(body));
  };
  if (url.pathname === "/healthz") return json(200, { ok: true });
  if (request.method === "GET" && url.pathname === "/__e2e/messages") {
    const to = url.searchParams.get("to");
    return json(200, to ? messages.filter((m) => m.to === to) : messages);
  }
  if (request.method === "POST" && url.pathname === "/webhook/commerce-email") {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => {
      const raw = Buffer.concat(chunks);
      const problem = verify(raw, request.headers["x-mb-signature"]);
      if (problem) return json(401, { error: problem });
      const body = JSON.parse(raw.toString("utf8"));
      const id = `n8n-${messages.length + 1}`;
      messages.unshift({ id, delivery_id: request.headers["x-mb-delivery-id"], ...body });
      json(200, { id });
    });
    return undefined;
  }
  return json(404, { error: "not found" });
});

server.listen(PORT, "127.0.0.1", () => console.log(`fake n8n on ${PORT}`));
