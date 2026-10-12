import "server-only";

import { cookies } from "next/headers";

import { CUSTOMER_SESSION_COOKIE, customerCookie, safeStorePath } from "./customer-cookies";
import type { StorefrontContext } from "./tenant";

/** O que a API devolve quando um login de cliente termina (Google, senha, link do e-mail). */
export interface SignedIn {
  session_token: string;
  expires_at: string;
  return_to: string;
  access_status: string | null;
}

/** Segundos até a sessão vencer, para o `maxAge` do cookie. */
export function sessionMaxAge(done: SignedIn): number {
  return Math.max(0, Math.floor((Date.parse(done.expires_at) - Date.now()) / 1000));
}

/**
 * Para onde a pessoa vai depois de entrar. Loja que só mostra o catálogo a cliente aprovado manda
 * quem ainda não foi aprovado para a tela de pedir acesso, em vez de um 403.
 */
export function destinationAfterSignIn(
  context: StorefrontContext | null,
  done: SignedIn,
  hasPass = false,
): string {
  const target = safeStorePath(done.return_to);
  // Quem já entrou pela senha da loja não precisa de aprovação para ver a vitrine.
  const needsAccess = context?.access_mode === "whitelist" && done.access_status !== "approved" && !hasPass;
  return needsAccess ? `/acesso-pendente?next=${encodeURIComponent(target)}` : target;
}

/** Grava a sessão no navegador (dentro de uma server action). */
export async function keepSession(done: SignedIn): Promise<void> {
  (await cookies()).set(CUSTOMER_SESSION_COOKIE, done.session_token, customerCookie(sessionMaxAge(done)));
}
