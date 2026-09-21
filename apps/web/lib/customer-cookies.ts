/**
 * Store customer cookies (tenant host only). Kept free of `next/headers` so the middleware
 * (edge runtime) can import the names.
 *
 * - `__Host-mb_sess`: opaque session token, 30 days sliding on the API side. Lax (customers
 *   arrive from WhatsApp/Instagram links); POSTs are checked for the store's own Origin.
 * - `__Host-mb_oidc`: random value binding a Google sign-in to the browser that started it
 *   (login CSRF), 10 minutes, cleared when the sign-in completes.
 */
export const CUSTOMER_SESSION_COOKIE = "__Host-mb_sess";
export const CUSTOMER_BINDING_COOKIE = "__Host-mb_oidc";
export const BINDING_MAX_AGE = 600;

export function customerCookie(maxAge: number) {
  return { httpOnly: true, secure: true, sameSite: "lax" as const, path: "/", maxAge };
}

/** Same-store relative path, or "/". Never an auth route (no loops) nor another host. */
export function safeStorePath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  if (next.startsWith("/auth/") || /[\r\n\t]/.test(next) || next.length > 512) return "/";
  return next;
}
