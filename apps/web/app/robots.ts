import type { MetadataRoute } from "next";
import { headers } from "next/headers";

import { lookupStorefrontContext } from "@/lib/context-cache";
import { isIndexable } from "@/lib/storefront";
import { classifyHost, normalizeHost } from "@/lib/tenant";

// Per host: robots.txt skips the middleware (matcher), so the tenant is resolved here.
export const dynamic = "force-dynamic";

const CLOSED: MetadataRoute.Robots = { rules: { userAgent: "*", disallow: "/" } };

export default async function robots(): Promise<MetadataRoute.Robots> {
  const host = normalizeHost((await headers()).get("host"));
  const kind = classifyHost(host, {
    panelHost: process.env.PANEL_HOST ?? "painel.muhbianco.com.br",
    platformBaseDomain: process.env.PLATFORM_BASE_DOMAIN ?? "loja.muhbianco.com.br",
  });
  if (!host || kind !== "storefront") return CLOSED;
  const lookup = await lookupStorefrontContext(host);
  if (lookup.kind !== "found" || !isIndexable(lookup.context)) return CLOSED;
  const origin = `https://${lookup.context.primary_host ?? host}`;
  return {
    rules: { userAgent: "*", allow: "/", disallow: ["/entrar", "/acesso-pendente", "/painel", "/cw-app", "/api/"] },
    sitemap: `${origin}/sitemap.xml`,
    host: origin,
  };
}
