import { afterEach, describe, expect, it, vi } from "vitest";

import { clearRefreshCache, refreshOnce, type RefreshResult } from "./refresh";
import {
  cookieOptions,
  needsRenewal,
  safeReturnPath,
  secondsUntilExpiry,
  withCookies,
} from "./token";

function jwt(claims: Record<string, unknown>): string {
  const encode = (value: unknown) =>
    Buffer.from(JSON.stringify(value)).toString("base64url");
  return `${encode({ alg: "HS256" })}.${encode(claims)}.signature`;
}

const NOW = 1_800_000_000_000;

describe("session tokens", () => {
  it("reads exp without trusting the signature", () => {
    expect(secondsUntilExpiry(jwt({ exp: NOW / 1000 + 600 }), NOW)).toBe(600);
    expect(secondsUntilExpiry(undefined, NOW)).toBe(-1);
    expect(secondsUntilExpiry("garbage", NOW)).toBe(-1);
    expect(secondsUntilExpiry(jwt({ sub: "x" }), NOW)).toBe(-1);
  });

  it("renews shortly before expiry and when the token is missing", () => {
    expect(needsRenewal(jwt({ exp: NOW / 1000 + 600 }), NOW)).toBe(false);
    expect(needsRenewal(jwt({ exp: NOW / 1000 + 30 }), NOW)).toBe(true);
    expect(needsRenewal(undefined, NOW)).toBe(true);
  });

  it("uses HttpOnly, Secure, SameSite=Strict cookies", () => {
    expect(cookieOptions(900)).toEqual({
      httpOnly: true,
      secure: true,
      sameSite: "strict",
      path: "/",
      maxAge: 900,
    });
  });

  it("rewrites the Cookie header for the current request", () => {
    const header = "__Host-mb_at=old; theme=dark; __Host-mb_rt=oldrt";
    expect(withCookies(header, { "__Host-mb_at": "new", "__Host-mb_rt": "newrt" })).toBe(
      "theme=dark; __Host-mb_at=new; __Host-mb_rt=newrt",
    );
    expect(withCookies(null, { a: "1" })).toBe("a=1");
  });

  it("only returns to same-origin relative paths after login", () => {
    expect(safeReturnPath("/ops/tenants")).toBe("/ops/tenants");
    expect(safeReturnPath("https://evil.test")).toBe("/");
    expect(safeReturnPath("//evil.test")).toBe("/");
    expect(safeReturnPath("/\\evil.test")).toBe("/");
    expect(safeReturnPath(undefined)).toBe("/");
  });
});

describe("single-flight refresh", () => {
  afterEach(() => clearRefreshCache());

  const renewed: RefreshResult = {
    kind: "renewed",
    pair: { access_token: "a2", refresh_token: "r2", expires_in: 900 },
  };

  it("calls the API once for concurrent requests carrying the same refresh token", async () => {
    const fetcher = vi.fn(async () => renewed);
    const results = await Promise.all([
      refreshOnce("r1", fetcher, NOW),
      refreshOnce("r1", fetcher, NOW + 10),
      refreshOnce("r1", fetcher, NOW + 5_000),
    ]);
    expect(fetcher).toHaveBeenCalledTimes(1);
    expect(results.every((result) => result === results[0])).toBe(true);
  });

  it("forgets the result after the reuse window", async () => {
    const fetcher = vi.fn(async () => renewed);
    await refreshOnce("r1", fetcher, NOW);
    await refreshOnce("r1", fetcher, NOW + 16_000);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });

  it("does not cache an outage, so the next request retries", async () => {
    const fetcher = vi
      .fn<(token: string) => Promise<RefreshResult>>()
      .mockRejectedValueOnce(new Error("ECONNREFUSED"))
      .mockResolvedValueOnce(renewed);
    expect(await refreshOnce("r1", fetcher, NOW)).toEqual({ kind: "unavailable" });
    expect(await refreshOnce("r1", fetcher, NOW + 100)).toEqual(renewed);
    expect(fetcher).toHaveBeenCalledTimes(2);
  });
});

describe("logout", () => {
  it("expires session cookies with the attributes a __Host- cookie requires", async () => {
    const { EXPIRED_COOKIE } = await import("./token");
    expect(EXPIRED_COOKIE).toMatchObject({ secure: true, path: "/", maxAge: 0 });
  });
});
