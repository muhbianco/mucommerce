import type { NextRequest, NextResponse } from "next/server";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import {
  CUSTOMER_BINDING_COOKIE,
  CUSTOMER_SESSION_COOKIE,
  customerCookie,
  safeStorePath,
} from "@/lib/customer-cookies";
import { relativeRedirect } from "@/lib/relative-redirect";
import { getStorefrontContext } from "@/lib/server-context";

interface Completed {
  session_token: string;
  expires_at: string;
  return_to: string;
  access_status: string | null;
}

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

  let done: Completed;
  try {
    done = await customerApi<Completed>("/internal/customer-auth/complete", {
      json: { hc: hc.slice(0, 128), binding, previous_session: previous },
      session: null,
    });
  } catch (error) {
    if (!(error instanceof CustomerApiError)) console.error("customer sign-in completion failed", error);
    return fail(error instanceof CustomerApiError && error.status < 500 ? "sessao_expirada" : "login_indisponivel");
  }

  // Whitelist stores send customers without approval to the access page instead of a 403.
  const context = await getStorefrontContext();
  const target = safeStorePath(done.return_to);
  const needsAccess = context?.access_mode === "whitelist" && done.access_status !== "approved";
  const destination = needsAccess ? `/acesso-pendente?next=${encodeURIComponent(target)}` : target;

  const maxAge = Math.max(0, Math.floor((Date.parse(done.expires_at) - Date.now()) / 1000));
  const response = relativeRedirect(destination);
  response.cookies.set(CUSTOMER_SESSION_COOKIE, done.session_token, customerCookie(maxAge));
  response.cookies.set(CUSTOMER_BINDING_COOKIE, "", customerCookie(0));
  response.headers.set("Referrer-Policy", "no-referrer");
  return response;
}
