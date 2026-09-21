import { randomBytes } from "node:crypto";

import { type NextRequest, NextResponse } from "next/server";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { BINDING_MAX_AGE, CUSTOMER_BINDING_COOKIE, customerCookie, safeStorePath } from "@/lib/customer-cookies";
import { relativeRedirect } from "@/lib/relative-redirect";

/**
 * "Entrar com Google" on the store: the API opens the sign-in flow (server-side, web token) and
 * this browser gets the random binding that must come back with it.
 */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const next = safeStorePath(request.nextUrl.searchParams.get("next"));
  const binding = randomBytes(32).toString("base64url");
  const version = (name: string) => {
    const value = Number(request.nextUrl.searchParams.get(name));
    return Number.isInteger(value) && value > 0 && value <= 100_000 ? value : undefined;
  };
  let authorizeUrl: string;
  try {
    ({ authorize_url: authorizeUrl } = await customerApi<{ authorize_url: string }>(
      "/internal/customer-auth/google/start",
      {
        json: { return_to: next, binding, terms_version: version("tv"), privacy_version: version("pv") },
        session: null,
      },
    ));
  } catch (error) {
    const reason =
      error instanceof CustomerApiError && error.status === 429 ? "muitas_tentativas" : "login_indisponivel";
    if (!(error instanceof CustomerApiError)) console.error("customer sign-in start failed", error);
    return relativeRedirect(`/entrar?${new URLSearchParams({ erro: reason, next }).toString()}`);
  }
  const response = NextResponse.redirect(authorizeUrl, 302);
  response.cookies.set(CUSTOMER_BINDING_COOKIE, binding, customerCookie(BINDING_MAX_AGE));
  response.headers.set("Cache-Control", "no-store");
  return response;
}
