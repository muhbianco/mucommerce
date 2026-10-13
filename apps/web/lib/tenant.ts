import type { PublicPayments } from "./store/installments";

/**
 * Host classification shared by the middleware and tests. Pure functions only.
 *
 * The tenant is NEVER taken from a query string, body or cookie: only the Host
 * header (as forwarded by Traefik) decides which store renders.
 */

/**
 * `panel`: painel da plataforma (painel.muhbianco.com.br), só da equipe MuhBianco.
 * `tenant_panel`: painel de uma loja (<slug>.painel.muhbianco.com.br ou painel.<domínio>).
 */
export type HostKind = "panel" | "tenant_panel" | "storefront" | "unknown";

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
  /** Loja "toda fechada": nem a página inicial aparece antes de entrar. */
  landing_locked?: boolean;
  /** A loja fechada tem senha: a porta oferece "tenho a senha" ao lado do login. */
  password_gate?: boolean;
  features: Record<string, boolean>;
  branding: Record<string, unknown>;
  seo: Record<string, unknown>;
  /** O que a loja oferece, como `public_fulfillment` monta do lado da API. */
  fulfillment: {
    modes?: string[];
    min_order_cents?: number;
    pickup_locations?: { id: string; name: string; address: string; instructions: string | null }[];
    delivery_zones?: { id: string; name: string; fee_cents: number; eta_minutes?: number | null }[];
    shipping?: { enabled?: boolean; free_above_cents?: number | null; handling_days?: number };
  } & Record<string, unknown>;
  /** Meios aceitos e teto de parcelamento, para a vitrine anunciar antes do carrinho. */
  payments?: PublicPayments;
  /** O que o checkout pede além do básico (a loja escolhe no painel). */
  checkout?: { require_whatsapp?: boolean };
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

// Next.js's own origin (`__NEXT_PRIVATE_ORIGIN`): `localhost:<port>` when it binds 0.0.0.0.
const SELF_HOST = /^(localhost|127\.0\.0\.1|0\.0\.0\.0|\[::1?\])(:\d+)?$/;

/**
 * The host a request is for. Traefik routes on Host and passes it through, so for any request
 * from outside that is the answer. The exception is the page a Server Action redirects to:
 * Next.js renders it with a fetch to its own origin, and Node's fetch replaces the Host it
 * forwards with that loopback address, leaving the browser's host only in X-Forwarded-Host.
 * Resolved by Host, that request is an unknown store and the redirect lands on the 404 page.
 *
 * X-Forwarded-Host is read only when Host is loopback, which Traefik never routes here; from
 * inside the network it grants nothing a caller could not get by sending Host itself.
 */
export function resolveRequestHost(
  host: string | null | undefined,
  forwardedHost: string | null | undefined,
): string | null {
  if (!SELF_HOST.test(host?.trim().toLowerCase() ?? "")) return normalizeHost(host);
  return normalizeHost(forwardedHost?.split(",")[0]) ?? normalizeHost(host);
}

export function classifyHost(host: string | null, rules: HostRules): HostKind {
  if (!host) return "unknown";
  if (host === rules.panelHost) return "panel";
  // O painel com domínio próprio da loja (painel.<domínio>) só se descobre pela API; aqui
  // entra como storefront e o middleware confere quando a vitrine não existe.
  if (host.endsWith(`.${rules.panelHost}`)) return "tenant_panel";
  return "storefront";
}

/** Caminhos que o painel de uma loja atende, já sem o prefixo `/painel`. */
const TENANT_PANEL_OPEN = ["/entrar", "/sso/start", "/sso/callback"];

/**
 * No painel de uma loja só existe aquela loja: `/` vai para ela, `/t/<outra>` e as páginas da
 * plataforma (lista de lojas, ops) não existem. A API continua autorizando por vínculo; isto
 * separa os endereços, para ninguém trabalhar numa loja pelo painel de outra.
 */
export function tenantPanelGate(pathname: string, tenantId: string): "ok" | "home" | "not_found" {
  const path = isPanelPath(pathname) ? pathname.slice(PANEL_PREFIX.length) || "/" : pathname;
  if (path === "/") return "home";
  if (TENANT_PANEL_OPEN.includes(path)) return "ok";
  const own = `/t/${tenantId}`;
  if (path === own || path.startsWith(own + "/")) return "ok";
  return "not_found";
}

/**
 * Paths that render without login even when the store is `whitelist`/`login_required`.
 *
 * `/ingresso/<código>` e `/validar/<código>` são links de capacidade: quem tem o código é quem
 * comprou (ou a portaria, que ainda passa pelo login do painel). Pedir login de cliente da loja
 * fechada para abri-los mandava o amigo que recebeu o ingresso, e a porta, para "Entrar".
 */
const PUBLIC_PREFIXES = [
  "/",
  "/eventos",
  "/politicas",
  "/entrar",
  "/acesso-pendente",
  "/auth",
  "/healthz",
  "/ingresso",
  "/validar",
];

export function isPublicStorefrontPath(pathname: string): boolean {
  if (pathname === "/") return true;
  return PUBLIC_PREFIXES.some((prefix) => prefix !== "/" && (pathname === prefix || pathname.startsWith(prefix + "/")));
}

/**
 * Rotas de dados da vitrine. Não são páginas: quem chama é `fetch`, e quem espera resposta é
 * JavaScript, não uma pessoa.
 */
const STORE_API_PREFIX = "/api/loja/";

export function isStoreApiPath(pathname: string): boolean {
  return pathname.startsWith(STORE_API_PREFIX);
}

export function requiresSession(pathname: string, accessMode: StorefrontContext["access_mode"]): boolean {
  return needsEntry(pathname, { access_mode: accessMode });
}

/** Na loja toda fechada, só a porta (e o que ela usa) abre sem entrar. */
const ENTRY_PREFIXES = ["/entrar", "/acesso-pendente", "/auth", "/politicas", "/healthz", "/ingresso", "/validar"];

/**
 * O caminho pede que o visitante tenha entrado — com a sessão de cliente ou com o passe da senha
 * da loja (docs/14-pedidos-lunares.md §3). O middleware só olha se um dos cookies existe; quem
 * decide de verdade é a API, e a página manda para a porta quando ela recusa.
 */
export function needsEntry(
  pathname: string,
  context: Pick<StorefrontContext, "access_mode" | "landing_locked">,
): boolean {
  if (context.access_mode === "public") return false;
  // Mandar `fetch` para a tela de login devolve HTML onde o navegador espera JSON. Estas rotas
  // fazem o próprio controle e respondem 401/403 — nunca um resultado parcial, que é o que
  // transformaria loja fechada em porta de raspagem.
  if (isStoreApiPath(pathname)) return false;
  if (context.landing_locked) {
    return !ENTRY_PREFIXES.some((prefix) => pathname === prefix || pathname.startsWith(prefix + "/"));
  }
  // Home aberta: produto exposto na home abre a própria página; a API recusa os outros e a
  // página manda para a porta.
  if (pathname.startsWith("/loja/produto/")) return false;
  return !isPublicStorefrontPath(pathname);
}

const PANEL_PREFIX = "/painel";

/** True for `/painel` and anything below it. */
export function isPanelPath(pathname: string): boolean {
  return pathname === PANEL_PREFIX || pathname.startsWith(PANEL_PREFIX + "/");
}

/**
 * On the panel host every path lives under `/painel` (`/` → `/painel`, `/ops` → `/painel/ops`),
 * so the storefront landing at `/` never renders there. Paths already under `/painel` stay as-is.
 */
export function panelRewritePath(pathname: string): string {
  if (isPanelPath(pathname)) return pathname;
  return pathname === "/" ? PANEL_PREFIX : PANEL_PREFIX + pathname;
}
