import { describe, expect, it } from "vitest";

import { classifyHost, isPublicStorefrontPath, normalizeHost, requiresSession } from "./tenant";

const rules = { panelHost: "painel.muhbianco.com.br", platformBaseDomain: "loja.muhbianco.com.br" };

describe("normalizeHost", () => {
  it("lowercases, strips port and trailing dot", () => {
    expect(normalizeHost("Lunares.COM.BR:443")).toBe("lunares.com.br");
    expect(normalizeHost("lunares.com.br.")).toBe("lunares.com.br");
  });
  it("rejects garbage", () => {
    expect(normalizeHost("")).toBeNull();
    expect(normalizeHost("[::1]")).toBeNull();
    expect(normalizeHost("bad_host.com")).toBeNull();
    expect(normalizeHost("-x.com")).toBeNull();
  });
});

describe("classifyHost", () => {
  it("routes the panel host and treats everything else as storefront", () => {
    expect(classifyHost("painel.muhbianco.com.br", rules)).toBe("panel");
    expect(classifyHost("lunares.com.br", rules)).toBe("storefront");
    expect(classifyHost("lunares.loja.muhbianco.com.br", rules)).toBe("storefront");
    expect(classifyHost(null, rules)).toBe("unknown");
  });
});

describe("access gating", () => {
  it("keeps landing, events, policies and auth public", () => {
    expect(isPublicStorefrontPath("/")).toBe(true);
    expect(isPublicStorefrontPath("/eventos/brownie-day")).toBe(true);
    expect(isPublicStorefrontPath("/auth/complete")).toBe(true);
    expect(isPublicStorefrontPath("/loja")).toBe(false);
    expect(isPublicStorefrontPath("/carrinho")).toBe(false);
  });
  it("requires a session only outside public paths and only when not public", () => {
    expect(requiresSession("/loja", "whitelist")).toBe(true);
    expect(requiresSession("/loja", "login_required")).toBe(true);
    expect(requiresSession("/loja", "public")).toBe(false);
    expect(requiresSession("/", "whitelist")).toBe(false);
  });
});
