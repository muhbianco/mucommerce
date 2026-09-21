import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const context = { tenant: { id: "t1", slug: "modelo" }, access_mode: "public" };

async function loadCache(maxEntries: number) {
  vi.resetModules();
  vi.stubEnv("CONTEXT_CACHE_MAX_ENTRIES", String(maxEntries));
  return import("./context-cache");
}

describe("storefront context cache", () => {
  beforeEach(() => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (_url: string, init: { headers: Record<string, string> }) => {
        const host = init.headers["X-Tenant-Host"] ?? "";
        if (host === "down.test") throw new Error("ECONNREFUSED");
        if (host === "suspensa.test") return new Response("{}", { status: 503 });
        if (host === "erro.test") return new Response("{}", { status: 500 });
        if (host.startsWith("loja")) return Response.json(context);
        return new Response("{}", { status: 404 });
      }),
    );
  });
  afterEach(() => {
    vi.unstubAllGlobals();
    vi.unstubAllEnvs();
  });

  it("classifies API answers", async () => {
    const cache = await loadCache(100);
    expect(await cache.lookupStorefrontContext("loja.test")).toEqual({ kind: "found", context });
    expect(await cache.lookupStorefrontContext("nada.test")).toEqual({ kind: "not_found" });
    expect(await cache.lookupStorefrontContext("suspensa.test")).toEqual({ kind: "suspended" });
    expect(await cache.lookupStorefrontContext("erro.test")).toEqual({ kind: "unavailable" });
    expect(await cache.lookupStorefrontContext("down.test")).toEqual({ kind: "unavailable" });
    // Outages are never cached, so the next request retries the API.
    expect(cache.contextCacheSize()).toBe(3);
  });

  it("stays bounded under many distinct Host headers", async () => {
    const cache = await loadCache(5);
    for (let i = 0; i < 50; i++) await cache.lookupStorefrontContext(`lixo${i}.test`);
    expect(cache.contextCacheSize()).toBe(5);
  });

  it("evicts the least recently used host first", async () => {
    const cache = await loadCache(2);
    await cache.lookupStorefrontContext("loja-a.test");
    await cache.lookupStorefrontContext("loja-b.test");
    await cache.lookupStorefrontContext("loja-a.test"); // hit: a becomes most recent
    await cache.lookupStorefrontContext("loja-c.test"); // evicts b
    const calls = vi.mocked(fetch).mock.calls.length;
    await cache.lookupStorefrontContext("loja-a.test");
    expect(vi.mocked(fetch).mock.calls.length).toBe(calls); // still cached
    await cache.lookupStorefrontContext("loja-b.test");
    expect(vi.mocked(fetch).mock.calls.length).toBe(calls + 1); // was evicted
  });
});
