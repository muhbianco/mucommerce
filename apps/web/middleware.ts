import { NextResponse, type NextRequest } from "next/server";

import { lookupStorefrontContext } from "@/lib/context-cache";
import { callRefresh, refreshOnce } from "@/lib/panel/refresh";
import {
  ACCESS_COOKIE,
  cookieOptions,
  EXPIRED_COOKIE,
  needsRenewal,
  REFRESH_COOKIE,
  REFRESH_TTL_SECONDS,
  type TokenPair,
  withCookies,
} from "@/lib/panel/token";
import { classifyHost, isPanelPath, panelRewritePath, requiresSession, resolveRequestHost } from "@/lib/tenant";

const SESSION_COOKIE = "mb_sess";
const TENANT_HEADERS = ["x-tenant-id", "x-tenant-slug", "x-tenant-host", "x-tenant-context", "x-host-kind"];

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|healthz|robots.txt).*)"],
};

function notFound(request: NextRequest): NextResponse {
  return NextResponse.rewrite(new URL("/_not-found", request.url), { status: 404 });
}

function unavailable(request: NextRequest): NextResponse {
  // 503 (not 404): a suspended store or an API outage must not look like a missing page.
  const response = NextResponse.rewrite(new URL("/indisponivel", request.url), { status: 503 });
  response.headers.set("Retry-After", "60");
  return response;
}

const PANEL_LOGIN = "/painel/entrar";

function isPrefetch(request: NextRequest): boolean {
  return (
    request.headers.get("next-router-prefetch") === "1" ||
    request.headers.get("purpose") === "prefetch"
  );
}

/**
 * Panel host: every path lives under /painel, never indexed. Renews the session before the
 * access token expires (single-flight, see lib/panel/refresh.ts) and ends it only when the API
 * rejects the refresh token; an API outage keeps the cookies.
 */
async function panel(request: NextRequest, headers: Headers): Promise<NextResponse> {
  const { pathname } = request.nextUrl;
  const target = panelRewritePath(pathname);
  const access = request.cookies.get(ACCESS_COOKIE)?.value;
  const refresh = request.cookies.get(REFRESH_COOKIE)?.value;

  let renewed: TokenPair | null = null;
  if (target !== PANEL_LOGIN && refresh && needsRenewal(access) && !isPrefetch(request)) {
    const forwardedFor = request.headers.get("x-forwarded-for");
    const result = await refreshOnce(refresh, (token) => callRefresh(token, forwardedFor));
    if (result.kind === "rejected") {
      const response = NextResponse.redirect(new URL("/entrar", request.url));
      response.cookies.set(ACCESS_COOKIE, "", EXPIRED_COOKIE);
      response.cookies.set(REFRESH_COOKIE, "", EXPIRED_COOKIE);
      response.headers.set("X-Robots-Tag", "noindex, nofollow");
      return response;
    }
    if (result.kind === "renewed") {
      renewed = result.pair;
      headers.set(
        "cookie",
        withCookies(headers.get("cookie"), {
          [ACCESS_COOKIE]: renewed.access_token,
          [REFRESH_COOKIE]: renewed.refresh_token,
        }),
      );
    }
  }

  let response: NextResponse;
  if (target === pathname) {
    response = NextResponse.next({ request: { headers } });
  } else {
    const url = request.nextUrl.clone();
    url.pathname = target;
    response = NextResponse.rewrite(url, { request: { headers } });
  }
  if (renewed) {
    response.cookies.set(ACCESS_COOKIE, renewed.access_token, cookieOptions(renewed.expires_in));
    response.cookies.set(REFRESH_COOKIE, renewed.refresh_token, cookieOptions(REFRESH_TTL_SECONDS));
  }
  response.headers.set("X-Robots-Tag", "noindex, nofollow");
  return response;
}

export async function middleware(request: NextRequest) {
  const rules = {
    panelHost: process.env.PANEL_HOST ?? "painel.muhbianco.com.br",
    platformBaseDomain: process.env.PLATFORM_BASE_DOMAIN ?? "loja.muhbianco.com.br",
  };
  const host = resolveRequestHost(request.headers.get("host"), request.headers.get("x-forwarded-host"));
  const kind = classifyHost(host, rules);
  const { pathname } = request.nextUrl;

  // Never trust tenant headers coming from outside; the middleware is the only writer.
  const headers = new Headers(request.headers);
  for (const name of TENANT_HEADERS) headers.delete(name);
  headers.set("x-host-kind", kind);

  if (kind === "unknown") return notFound(request);

  if (kind === "panel") return panel(request, headers);

  // The panel only exists on the panel host.
  if (isPanelPath(pathname)) return notFound(request);

  // Storefront: resolve the tenant by Host through the API (cached).
  const lookup = host ? await lookupStorefrontContext(host) : ({ kind: "not_found" } as const);
  if (lookup.kind === "not_found") return notFound(request);
  if (lookup.kind !== "found") return unavailable(request);
  const { context } = lookup;

  headers.set("x-tenant-id", context.tenant.id);
  headers.set("x-tenant-slug", context.tenant.slug);
  headers.set("x-tenant-host", host ?? "");
  headers.set("x-tenant-context", Buffer.from(JSON.stringify(context), "utf-8").toString("base64"));

  if (requiresSession(pathname, context.access_mode) && !request.cookies.get(SESSION_COOKIE)) {
    const login = new URL("/entrar", request.url);
    login.searchParams.set("next", pathname);
    return NextResponse.redirect(login);
  }

  return NextResponse.next({ request: { headers } });
}
