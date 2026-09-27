import { type NextRequest, NextResponse } from "next/server";

import { encodeSsoState, newSsoState, pkceChallenge, SSO_COOKIE, SSO_COOKIE_MAX_AGE } from "@/lib/panel/sso";
import { safeReturnPath } from "@/lib/panel/token";
import { resolveRequestHost } from "@/lib/tenant";

/** Start the MuhBianco sign-in (Google via the MuhBianco account). See lib/panel/sso.ts. */
export function GET(request: NextRequest): NextResponse {
  const sso = newSsoState(safeReturnPath(request.nextUrl.searchParams.get("next")));
  const accounts = process.env.MUHBIANCO_ACCOUNTS_URL ?? "https://api.muhbianco.com.br";
  const target = new URL("/api/v1/auth/google/login", accounts);
  target.searchParams.set("app", "commerce");
  target.searchParams.set("client_state", sso.state);
  target.searchParams.set("code_challenge", pkceChallenge(sso.verifier));
  // A conta MuhBianco devolve o login para o painel que pediu (o da plataforma ou o da loja);
  // ela confere o host antes de mandar o código para cá.
  const host = resolveRequestHost(request.headers.get("host"), request.headers.get("x-forwarded-host"));
  if (host) target.searchParams.set("return_host", host);

  const response = NextResponse.redirect(target, 302);
  // Lax (not Strict): the callback arrives as a top-level navigation from another site.
  response.cookies.set(SSO_COOKIE, encodeSsoState(sso), {
    httpOnly: true,
    secure: true,
    sameSite: "lax",
    path: "/",
    maxAge: SSO_COOKIE_MAX_AGE,
  });
  response.headers.set("Cache-Control", "no-store");
  return response;
}
