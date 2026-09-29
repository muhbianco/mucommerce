import { type NextRequest, NextResponse } from "next/server";

import { api, ApiError } from "@/lib/panel/api";
import { continuePage, decodeSsoState, sameState, SSO_COOKIE } from "@/lib/panel/sso";
import {
  ACCESS_COOKIE,
  cookieOptions,
  REFRESH_COOKIE,
  REFRESH_TTL_SECONDS,
  type TokenPair,
} from "@/lib/panel/token";

const EXPIRED_SSO = { httpOnly: true, secure: true, sameSite: "lax" as const, path: "/", maxAge: 0 };

/**
 * Volta para a tela de entrar com o motivo.
 *
 * O `Location` é **relativo**, e isto não é estilo: a middleware reescreve `painel.<domínio>/x`
 * para `/painel/x`, e aqui dentro `request.url` já é a URL interna — o host vira o do servidor e
 * perde o `painel.`. `new URL("/entrar", request.url)` mandava o navegador para um endereço que
 * não é loja nem painel, ou seja, 404 em vez do aviso de login. Endereço relativo o navegador
 * resolve contra a URL que ele realmente pediu, que é a certa.
 */
function fail(_request: NextRequest, reason: string): NextResponse {
  const response = new NextResponse(null, {
    status: 302,
    headers: { Location: `/entrar?erro=${reason}`, "Cache-Control": "no-store" },
  });
  response.cookies.set(SSO_COOKIE, "", EXPIRED_SSO);
  return response;
}

/** The MuhBianco login sends the browser back here with a one-time code. */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const params = request.nextUrl.searchParams;
  const saved = decodeSsoState(request.cookies.get(SSO_COOKIE)?.value);
  const code = params.get("code");
  if (params.get("error") || !saved || !code || !sameState(params.get("state"), saved.state)) {
    return fail(request, "sso");
  }

  let pair: TokenPair;
  try {
    pair = await api<TokenPair>("/auth/sso/exchange", {
      token: null,
      json: { code, code_verifier: saved.verifier, purpose: "panel" },
    });
  } catch (error) {
    // Network failure or timeout reaching the API is an outage, not a bad login.
    const outage = !(error instanceof ApiError) || error.status >= 500;
    if (outage) console.error("panel sso exchange failed", error instanceof Error ? error.name : error);
    return fail(request, outage ? "sso_indisponivel" : "sso");
  }

  // 200 page instead of a redirect: see continuePage (SameSite=Strict session cookies).
  const response = new NextResponse(continuePage(saved.next), {
    status: 200,
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
  response.cookies.set(ACCESS_COOKIE, pair.access_token, cookieOptions(pair.expires_in));
  response.cookies.set(REFRESH_COOKIE, pair.refresh_token, cookieOptions(REFRESH_TTL_SECONDS));
  response.cookies.set(SSO_COOKIE, "", EXPIRED_SSO);
  return response;
}
