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
/** Pending "CONFIRMAR <código>" wa.me link, 10 minutes (the challenge's lifetime). */
export const WHATSAPP_LINK_COOKIE = "__Host-mb_wa";
/**
 * CEP do frete na vitrine (F6), 30 dias: quem calculou o frete num produto vê o dos outros sem
 * digitar de novo. httpOnly e fora da URL: CEP é dado pessoal, não vai para log nem histórico.
 */
export const CEP_COOKIE = "__Host-mb_cep";
export const CEP_MAX_AGE = 30 * 24 * 3600;
/**
 * "Acabei de pedir o frete", 30 s: a página logo depois do "Calcular" cota antes de responder,
 * em vez de mandar o resultado por streaming (que sem JavaScript nunca entra na página).
 */
export const CEP_FRESH_COOKIE = "__Host-mb_cep_agora";
export const CEP_FRESH_MAX_AGE = 30;

export function customerCookie(maxAge: number) {
  return { httpOnly: true, secure: true, sameSite: "lax" as const, path: "/", maxAge };
}

/** Same-store relative path, or "/". Never an auth route (no loops) nor another host. */
export function safeStorePath(next: string | null | undefined): string {
  if (!next || !next.startsWith("/") || next.startsWith("//") || next.startsWith("/\\")) return "/";
  if (next.startsWith("/auth/") || /[\r\n\t]/.test(next) || next.length > 512) return "/";
  return next;
}
