import { describe, expect, it } from "vitest";

import {
  classifyHost,
  isPanelPath,
  isPublicStorefrontPath,
  normalizeHost,
  panelRewritePath,
  requiresSession,
  resolveRequestHost,
} from "./tenant";

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

describe("resolveRequestHost", () => {
  it("routes on Host for requests from outside, ignoring X-Forwarded-Host", () => {
    expect(resolveRequestHost("painel.muhbianco.com.br", "lunares.com.br")).toBe("painel.muhbianco.com.br");
    expect(resolveRequestHost("Lunares.com.br:443", null)).toBe("lunares.com.br");
  });
  it("recovers the browser host on Next's own fetch after a Server Action redirect", () => {
    expect(resolveRequestHost("localhost:3000", "painel.muhbianco.com.br")).toBe("painel.muhbianco.com.br");
    expect(resolveRequestHost("127.0.0.1:3000", "lunares.com.br, proxy.internal")).toBe("lunares.com.br");
    expect(resolveRequestHost("[::1]:3000", "painel.muhbianco.com.br")).toBe("painel.muhbianco.com.br");
  });
  it("keeps a loopback Host when the forwarded one is missing or garbage", () => {
    expect(resolveRequestHost("localhost:3000", null)).toBe("localhost");
    expect(resolveRequestHost("localhost:3000", "bad_host.com")).toBe("localhost");
    expect(resolveRequestHost(null, "painel.muhbianco.com.br")).toBeNull();
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

describe("panel routing", () => {
  it("serves every panel-host path from /painel", () => {
    expect(panelRewritePath("/")).toBe("/painel");
    expect(panelRewritePath("/ops/tenants")).toBe("/painel/ops/tenants");
    expect(panelRewritePath("/painel")).toBe("/painel");
    expect(panelRewritePath("/painel/t/abc")).toBe("/painel/t/abc");
  });
  it("recognises panel paths without matching lookalikes", () => {
    expect(isPanelPath("/painel")).toBe(true);
    expect(isPanelPath("/painel/ops")).toBe(true);
    expect(isPanelPath("/painelx")).toBe(false);
    expect(isPanelPath("/loja")).toBe(false);
  });
});
