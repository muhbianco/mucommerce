import "server-only";

import { cookies } from "next/headers";

import { CUSTOMER_SESSION_COOKIE } from "./customer-cookies";
import type { StorefrontContext } from "./tenant";

/** Why a signed-in customer cannot see the catalog yet (403 codes of the API). */
export type AccessProblem = "access_required" | "access_pending" | "access_blocked";

export type StoreResult<T> =
  | { kind: "ok"; data: T }
  | { kind: "not_found" }
  | { kind: "login_required" }
  | { kind: AccessProblem };

const ACCESS_PROBLEMS = new Set<string>(["access_required", "access_pending", "access_blocked"]);

export const PUBLIC_REVALIDATE_SECONDS = 60;

/**
 * Server-side call to the storefront API as the web service: `X-Tenant-Host` picks the tenant,
 * the internal token only authenticates the web (it never grants catalog access).
 *
 * Public stores are cached for 60 s and never carry a customer session; any other access mode
 * forwards the customer's session (`X-Customer-Session`, honoured by the API only with the web
 * token) and is never cached. The host is also part of the URL so cached entries can never be
 * shared between tenants, whatever the fetch cache does with headers.
 */
export async function storefrontApi<T>(
  context: StorefrontContext,
  path: string,
  params: Record<string, string | undefined> = {},
): Promise<StoreResult<T>> {
  const host = context.host ?? context.primary_host ?? "";
  const base = process.env.COMMERCE_API_INTERNAL_URL ?? "http://127.0.0.1:8000";
  const query = new URLSearchParams({ _host: host });
  for (const [key, value] of Object.entries(params)) if (value) query.set(key, value);
  const cache =
    context.access_mode === "public"
      ? ({ next: { revalidate: PUBLIC_REVALIDATE_SECONDS } } as const)
      : ({ cache: "no-store" } as const);
  const requestHeaders: Record<string, string> = {
    "X-Tenant-Host": host,
    "X-Internal-Token": process.env.INTERNAL_TOKEN_WEB ?? "",
    Accept: "application/json",
  };
  if (context.access_mode !== "public") {
    const session = (await cookies()).get(CUSTOMER_SESSION_COOKIE)?.value;
    if (session) requestHeaders["X-Customer-Session"] = session;
  }
  const response = await fetch(`${base}/api/v1/storefront${path}?${query.toString()}`, {
    headers: requestHeaders,
    signal: AbortSignal.timeout(5000),
    ...cache,
  });
  if (response.status === 404) return { kind: "not_found" };
  if (response.status === 401) return { kind: "login_required" };
  if (response.status === 403) {
    const code = ((await response.json().catch(() => ({}))) as { error?: { code?: string } }).error?.code;
    if (code && ACCESS_PROBLEMS.has(code)) return { kind: code as AccessProblem };
  }
  if (!response.ok) throw new Error(`storefront API ${path} answered ${response.status}`);
  return { kind: "ok", data: (await response.json()) as T };
}
