import type { StorefrontContext } from "./tenant";

/**
 * Per-instance TTL cache of storefront contexts keyed by host.
 * Middleware and server components both read through here; the API is the source of truth.
 */
const ttlMs = Number(process.env.CONTEXT_CACHE_TTL_MS ?? 60_000);
const store = new Map<string, { expiresAt: number; value: StorefrontContext | null }>();

export async function fetchStorefrontContext(host: string): Promise<StorefrontContext | null> {
  const cached = store.get(host);
  if (cached && cached.expiresAt > Date.now()) return cached.value;

  const base = process.env.COMMERCE_API_INTERNAL_URL ?? "http://127.0.0.1:8000";
  const token = process.env.INTERNAL_TOKEN_WEB ?? "";
  let value: StorefrontContext | null = null;
  try {
    const response = await fetch(`${base}/api/v1/internal/storefront/context`, {
      headers: { "X-Tenant-Host": host, "X-Internal-Token": token },
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
    if (response.ok) {
      value = (await response.json()) as StorefrontContext;
    } else if (response.status === 503) {
      // Suspended tenant: cache briefly as null-with-reason via a marker on the record.
      value = null;
    }
  } catch {
    // Network error: do not cache; fall through and let the caller show a neutral page.
    return null;
  }
  store.set(host, { expiresAt: Date.now() + (value ? ttlMs : Math.min(ttlMs, 10_000)), value });
  return value;
}

export function clearContextCache(): void {
  store.clear();
}
