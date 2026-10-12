import type { NextRequest, NextResponse } from "next/server";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_BINDING_COOKIE, CUSTOMER_SESSION_COOKIE, customerCookie } from "@/lib/customer-cookies";
import { destinationAfterSignIn, sessionMaxAge, type SignedIn } from "@/lib/customer-signin";
import { relativeRedirect } from "@/lib/relative-redirect";
import { getStorefrontContext } from "@/lib/server-context";

/** Google → api-commerce callback → here, with the one-time handoff code: open the session. */
export async function GET(request: NextRequest): Promise<NextResponse> {
  const hc = request.nextUrl.searchParams.get("hc");
  const binding = request.cookies.get(CUSTOMER_BINDING_COOKIE)?.value;
  const previous = request.cookies.get(CUSTOMER_SESSION_COOKIE)?.value ?? null;
  const fail = (reason: string) => {
    const back = relativeRedirect(`/entrar?erro=${reason}`);
    back.cookies.set(CUSTOMER_BINDING_COOKIE, "", customerCookie(0));
    return back;
  };
  if (!hc || !binding) return fail("sessao_expirada");

  let done: SignedIn;
  try {
    done = await customerApi<SignedIn>("/internal/customer-auth/complete", {
      json: { hc: hc.slice(0, 128), binding, previous_session: previous },
      session: null,
    });
  } catch (error) {
    if (!(error instanceof CustomerApiError)) console.error("customer sign-in completion failed", error);
    return fail(error instanceof CustomerApiError && error.status < 500 ? "sessao_expirada" : "login_indisponivel");
  }

  const response = relativeRedirect(destinationAfterSignIn(await getStorefrontContext(), done));
  response.cookies.set(CUSTOMER_SESSION_COOKIE, done.session_token, customerCookie(sessionMaxAge(done)));
  response.cookies.set(CUSTOMER_BINDING_COOKIE, "", customerCookie(0));
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
