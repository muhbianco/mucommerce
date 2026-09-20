import { NextResponse, type NextRequest } from "next/server";

import { fetchStorefrontContext } from "@/lib/context-cache";
import { classifyHost, normalizeHost, requiresSession } from "@/lib/tenant";

const SESSION_COOKIE = "mb_sess";
const TENANT_HEADERS = ["x-tenant-id", "x-tenant-slug", "x-tenant-host", "x-tenant-context", "x-host-kind"];

export const config = {
  matcher: ["/((?!_next/static|_next/image|favicon.ico|healthz|robots.txt).*)"],
};

export async function middleware(request: NextRequest) {
  const rules = {
    panelHost: process.env.PANEL_HOST ?? "painel.muhbianco.com.br",
    platformBaseDomain: process.env.PLATFORM_BASE_DOMAIN ?? "loja.muhbianco.com.br",
  };
  const host = normalizeHost(request.headers.get("host"));
  const kind = classifyHost(host, rules);

  // Never trust tenant headers coming from outside; the middleware is the only writer.
  const headers = new Headers(request.headers);
  for (const name of TENANT_HEADERS) headers.delete(name);
  headers.set("x-host-kind", kind);

  if (kind === "unknown") {
    return NextResponse.rewrite(new URL("/_not-found", request.url), { status: 404 });
  }

  if (kind === "panel") {
    return NextResponse.next({ request: { headers } });
  }

  // Storefront: resolve the tenant by Host through the API (cached).
  const context = host ? await fetchStorefrontContext(host) : null;
  if (!context) {
    return NextResponse.rewrite(new URL("/_not-found", request.url), { status: 404 });
  }

  headers.set("x-tenant-id", context.tenant.id);
  headers.set("x-tenant-slug", context.tenant.slug);
  headers.set("x-tenant-host", host ?? "");
  headers.set("x-tenant-context", Buffer.from(JSON.stringify(context), "utf-8").toString("base64"));

  const { pathname } = request.nextUrl;
  if (requiresSession(pathname, context.access_mode) && !request.cookies.get(SESSION_COOKIE)) {
    const login = new URL("/entrar", request.url);
    login.searchParams.set("next", pathname);
    return NextResponse.redirect(login);
  }

  return NextResponse.next({ request: { headers } });
}
