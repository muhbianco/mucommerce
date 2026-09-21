/**
 * Panel session tokens: pure helpers shared by the middleware and server code (no Next imports,
 * so they run in the edge runtime and in vitest).
 *
 * The API issues a 15-minute access JWT and a rotating refresh token. Both live in `__Host-`
 * cookies on the panel host only: HttpOnly (never readable by JS), Secure, SameSite=Strict.
 */

export const ACCESS_COOKIE = "__Host-mb_at";
export const REFRESH_COOKIE = "__Host-mb_rt";
export const REFRESH_TTL_SECONDS = 14 * 24 * 60 * 60;
/** Renew a little before expiry so a request never reaches the API with a token that just died. */
export const RENEW_BEFORE_SECONDS = 60;

export interface TokenPair {
  access_token: string;
  refresh_token: string;
  expires_in: number;
}

export interface CookieOptions {
  httpOnly: true;
  secure: true;
  sameSite: "strict";
  path: "/";
  maxAge: number;
}

export function cookieOptions(maxAge: number): CookieOptions {
  return { httpOnly: true, secure: true, sameSite: "strict", path: "/", maxAge };
}

/**
 * Options that expire a session cookie. A `__Host-` cookie can only be overwritten by a
 * Set-Cookie carrying `Secure` and `Path=/`; a plain delete (no Secure) is ignored by the
 * browser and the session silently survives logout.
 */
export const EXPIRED_COOKIE: CookieOptions = cookieOptions(0);

/**
 * Seconds until the JWT's `exp`, or -1 when missing/unreadable. No signature check on purpose:
 * this only decides when to renew; the API verifies every token it receives.
 */
export function secondsUntilExpiry(jwt: string | undefined, nowMs: number = Date.now()): number {
  const payload = jwt?.split(".")[1];
  if (!payload) return -1;
  try {
    const base64 = payload.replace(/-/g, "+").replace(/_/g, "/");
    const padded = base64.padEnd(Math.ceil(base64.length / 4) * 4, "=");
    const claims = JSON.parse(atob(padded)) as { exp?: unknown };
    return typeof claims.exp === "number" ? claims.exp - Math.floor(nowMs / 1000) : -1;
  } catch {
    return -1;
  }
}

export function needsRenewal(accessToken: string | undefined, nowMs: number = Date.now()): boolean {
  return secondsUntilExpiry(accessToken, nowMs) < RENEW_BEFORE_SECONDS;
}

/**
 * Rewrite a `Cookie` request header with new values, so server components rendering this same
 * request already see the renewed session (Set-Cookie only reaches the browser afterwards).
 */
export function withCookies(header: string | null, values: Record<string, string>): string {
  const kept = (header ?? "")
    .split(";")
    .map((part) => part.trim())
    .filter((part) => part && !((part.split("=")[0] ?? "") in values));
  return [...kept, ...Object.entries(values).map(([name, value]) => `${name}=${value}`)].join("; ");
}

/** Only relative, same-origin return paths are honoured after login. */
export function safeReturnPath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  return next;
}
