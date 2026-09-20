/**
 * Host classification shared by the middleware and tests. Pure functions only.
 *
 * The tenant is NEVER taken from a query string, body or cookie: only the Host
 * header (as forwarded by Traefik) decides which store renders.
 */

export type HostKind = "panel" | "storefront" | "unknown";

export interface HostRules {
  panelHost: string;
  platformBaseDomain: string;
}

export interface StorefrontContext {
  tenant: {
    id: string;
    slug: string;
    name: string;
    status: string;
    timezone: string;
    locale: string;
    currency: string;
  };
  host: string | null;
  primary_host: string | null;
  access_mode: "public" | "login_required" | "whitelist";
  features: Record<string, boolean>;
  branding: Record<string, unknown>;
  seo: Record<string, unknown>;
  fulfillment: Record<string, unknown>;
  chatwoot_url?: string | null;
}

const HOST_LABEL = /^(?!-)[a-z0-9-]{1,63}(?<!-)$/;

/** Lowercase, strip port and trailing dot. Returns null for garbage. */
export function normalizeHost(raw: string | null | undefined): string | null {
  if (!raw) return null;
  let host = raw.trim().toLowerCase();
  if (host.startsWith("[")) return null;
  host = host.split(":")[0] ?? "";
  host = host.replace(/\.$/, "");
  if (!host || host.length > 253) return null;
  const labels = host.split(".");
  if (labels.some((label) => !HOST_LABEL.test(label))) return null;
  // `localhost` is allowed in development only (single label).
  if (labels.length < 2 && host !== "localhost") return null;
  return host;
}

export function classifyHost(host: string | null, rules: HostRules): HostKind {
  if (!host) return "unknown";
  if (host === rules.panelHost) return "panel";
  return "storefront";
}

/** Paths that render without login even when the store is `whitelist`/`login_required`. */
const PUBLIC_PREFIXES = ["/", "/eventos", "/politicas", "/entrar", "/acesso-pendente", "/auth", "/healthz"];

export function isPublicStorefrontPath(pathname: string): boolean {
  if (pathname === "/") return true;
  return PUBLIC_PREFIXES.some((prefix) => prefix !== "/" && (pathname === prefix || pathname.startsWith(prefix + "/")));
}

export function requiresSession(pathname: string, accessMode: StorefrontContext["access_mode"]): boolean {
  if (accessMode === "public") return false;
  return !isPublicStorefrontPath(pathname);
}
