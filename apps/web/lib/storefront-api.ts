import "server-only";

import type { StorefrontContext } from "./tenant";

export type StoreResult<T> =
  | { kind: "ok"; data: T }
  | { kind: "not_found" }
  | { kind: "login_required" };

export const PUBLIC_REVALIDATE_SECONDS = 60;

/**
 * Server-side call to the storefront API as the web service: `X-Tenant-Host` picks the tenant,
 * the internal token only authenticates the web (it never grants catalog access).
 *
 * Public stores are cached for 60 s; any other access mode is never cached. The host is also
 * part of the URL so cached entries can never be shared between tenants, whatever the fetch
 * cache does with headers.
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
  const response = await fetch(`${base}/api/v1/storefront${path}?${query.toString()}`, {
    headers: {
      "X-Tenant-Host": host,
      "X-Internal-Token": process.env.INTERNAL_TOKEN_WEB ?? "",
      Accept: "application/json",
    },
    signal: AbortSignal.timeout(5000),
    ...cache,
  });
  if (response.status === 404) return { kind: "not_found" };
  if (response.status === 401) return { kind: "login_required" };
  if (!response.ok) throw new Error(`storefront API ${path} answered ${response.status}`);
  return { kind: "ok", data: (await response.json()) as T };
}
