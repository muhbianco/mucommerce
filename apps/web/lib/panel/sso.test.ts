import { describe, expect, it } from "vitest";

import {
  continuePage,
  decodeSsoState,
  encodeSsoState,
  newSsoState,
  pkceChallenge,
  sameState,
} from "./sso";

describe("PKCE", () => {
  it("matches the RFC 7636 appendix B example", () => {
    expect(pkceChallenge("dBjftJeZ4CVP-mB92K27uhbUJU1p1r_wW1gFWFOEjXk")).toBe(
      "E9Melhoa2OwvFrEMTJguCHaoeK1t8URWbuGJSstw-cM",
    );
  });

  it("issues verifiers inside the allowed length and alphabet", () => {
    const { verifier, state } = newSsoState("/");
    expect(verifier).toMatch(/^[A-Za-z0-9_-]{43,128}$/);
    expect(state.length).toBeGreaterThanOrEqual(32);
  });
});

describe("state cookie", () => {
  it("round-trips and rejects garbage", () => {
    const value = newSsoState("/t/abc");
    expect(decodeSsoState(encodeSsoState(value))).toEqual(value);
    expect(decodeSsoState("lixo")).toBeNull();
    expect(decodeSsoState(undefined)).toBeNull();
    expect(decodeSsoState(Buffer.from('{"state":1}').toString("base64url"))).toBeNull();
  });

  it("compares states exactly", () => {
    expect(sameState("abc", "abc")).toBe(true);
    expect(sameState("abd", "abc")).toBe(false);
    expect(sameState("ab", "abc")).toBe(false);
    expect(sameState(null, "abc")).toBe(false);
  });
});

describe("continue page", () => {
  it("escapes the target path", () => {
    const html = continuePage('/t/x"><script>alert(1)</script>');
    expect(html).not.toContain("<script>");
    expect(html).toContain('url=/t/x&quot;&gt;&lt;script&gt;');
  });
});
