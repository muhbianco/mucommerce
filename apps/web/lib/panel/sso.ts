/**
 * Sign-in with the MuhBianco account (api-agents): authorization code + PKCE (RFC 7636).
 *
 * /sso/start keeps a random `state` and the PKCE `verifier` in a short-lived HttpOnly cookie and
 * sends the browser to the MuhBianco login with only the `challenge`. /sso/callback checks the
 * state, then the panel server redeems the one-time code with the verifier. A stolen code is
 * useless without the verifier, which never leaves this server and the browser cookie.
 *
 * Pure functions (node:crypto only): tested with vitest.
 */

import { createHash, randomBytes, timingSafeEqual } from "node:crypto";

export const SSO_COOKIE = "__Host-mb_sso";
export const SSO_COOKIE_MAX_AGE = 600;

export interface SsoState {
  state: string;
  verifier: string;
  next: string;
}

export function randomUrlToken(bytes: number): string {
  return randomBytes(bytes).toString("base64url");
}

/** S256 challenge: base64url(sha256(verifier)), no padding. */
export function pkceChallenge(verifier: string): string {
  return createHash("sha256").update(verifier, "ascii").digest("base64url");
}

export function newSsoState(next: string): SsoState {
  return { state: randomUrlToken(24), verifier: randomUrlToken(48), next };
}

export function encodeSsoState(value: SsoState): string {
  return Buffer.from(JSON.stringify(value), "utf-8").toString("base64url");
}

export function decodeSsoState(raw: string | undefined): SsoState | null {
  if (!raw || raw.length > 1024) return null;
  try {
    const value = JSON.parse(Buffer.from(raw, "base64url").toString("utf-8")) as Partial<SsoState>;
    if (typeof value.state !== "string" || typeof value.verifier !== "string" || typeof value.next !== "string") {
      return null;
    }
    return { state: value.state, verifier: value.verifier, next: value.next };
  } catch {
    return null;
  }
}

/** Constant-time comparison of the returned state with the one we issued. */
export function sameState(received: string | null, expected: string): boolean {
  if (!received) return false;
  const a = Buffer.from(received);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

/** Minimal page that navigates to `path` from the panel itself. A redirect chain that started
 * on another site would not carry the SameSite=Strict session cookies; a same-site navigation
 * does. */
export function continuePage(path: string): string {
  const escaped = path.replace(/&/g, "&amp;").replace(/"/g, "&quot;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  return `<!doctype html><html lang="pt-BR"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=${escaped}"><title>Entrando…</title></head><body><p>Entrando… <a href="${escaped}">continuar</a></p></body></html>`;
}
