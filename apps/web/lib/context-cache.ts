import type { StorefrontContext } from "./tenant";

/**
 * Per-instance TTL + LRU cache of storefront contexts keyed by host.
 * Middleware and server components both read through here; the API is the source of truth.
 *
 * Bounded on purpose: the key is the request's Host header, which anyone can set, so an
 * unbounded map would grow with every garbage host an attacker sends.
 */
export type ContextLookup =
  | { kind: "found"; context: StorefrontContext }
  | { kind: "not_found" }
  | { kind: "suspended" }
  | { kind: "unavailable" };

type CachedLookup = Exclude<ContextLookup, { kind: "unavailable" }>;

const ttlMs = Number(process.env.CONTEXT_CACHE_TTL_MS ?? 60_000);
const negativeTtlMs = Math.min(ttlMs, 10_000);
const maxEntries = Number(process.env.CONTEXT_CACHE_MAX_ENTRIES ?? 1_000);
const store = new Map<string, { expiresAt: number; value: CachedLookup }>();

function remember(host: string, value: CachedLookup): void {
  store.delete(host);
  store.set(host, {
    expiresAt: Date.now() + (value.kind === "found" ? ttlMs : negativeTtlMs),
    value,
  });
  // Map iterates in insertion order: the first key is the least recently used.
  while (store.size > maxEntries) {
    const oldest = store.keys().next().value;
    if (oldest === undefined) break;
    store.delete(oldest);
  }
}

export async function lookupStorefrontContext(host: string): Promise<ContextLookup> {
  const cached = store.get(host);
  if (cached && cached.expiresAt > Date.now()) {
    store.delete(host); // refresh recency
    store.set(host, cached);
    return cached.value;
  }

  const base = process.env.COMMERCE_API_INTERNAL_URL ?? "http://127.0.0.1:8000";
  const token = process.env.INTERNAL_TOKEN_WEB ?? "";
  let response: Response;
  try {
    response = await fetch(`${base}/api/v1/internal/storefront/context`, {
      headers: { "X-Tenant-Host": host, "X-Internal-Token": token },
      cache: "no-store",
      signal: AbortSignal.timeout(3000),
    });
  } catch {
    // Network error or timeout: not cached, the next request tries again.
    return { kind: "unavailable" };
  }

  let value: CachedLookup;
  if (response.ok) {
    value = { kind: "found", context: (await response.json()) as StorefrontContext };
  } else if (response.status === 404) {
    value = { kind: "not_found" };
  } else if (response.status === 503) {
    value = { kind: "suspended" };
  } else {
    return { kind: "unavailable" }; // 401 (token), 5xx: an operator problem, not a missing store
  }
  remember(host, value);
  return value;
}

export function clearContextCache(): void {
  store.clear();
}

export function contextCacheSize(): number {
  return store.size;
}
