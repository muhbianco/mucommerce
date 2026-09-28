import { describe, expect, it } from "vitest";

import {
  classifyHost,
  isPanelPath,
  isPublicStorefrontPath,
  normalizeHost,
  panelRewritePath,
  requiresSession,
  resolveRequestHost,
  tenantPanelGate,
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
  it("sends <slug>.painel.* to the store's own panel", () => {
    expect(classifyHost("lunares.painel.muhbianco.com.br", rules)).toBe("tenant_panel");
    // Custom panel domains are found through the API, not by name.
    expect(classifyHost("painel.lunares.com.br", rules)).toBe("storefront");
  });
});

describe("tenantPanelGate", () => {
  const id = "0192a1b2-0000-7000-8000-000000000001";
  it("sends the root to the store and serves only that store", () => {
    expect(tenantPanelGate("/", id)).toBe("home");
    expect(tenantPanelGate("/painel", id)).toBe("home");
    expect(tenantPanelGate(`/t/${id}`, id)).toBe("ok");
    expect(tenantPanelGate(`/t/${id}/pedidos/abc`, id)).toBe("ok");
    expect(tenantPanelGate(`/painel/t/${id}/produtos`, id)).toBe("ok");
  });
  it("keeps login open and hides other stores and platform pages", () => {
    expect(tenantPanelGate("/entrar", id)).toBe("ok");
    expect(tenantPanelGate("/sso/callback", id)).toBe("ok");
    expect(tenantPanelGate("/t/0192a1b2-0000-7000-8000-000000000002", id)).toBe("not_found");
    expect(tenantPanelGate(`/t/${id}x`, id)).toBe("not_found");
    expect(tenantPanelGate("/ops", id)).toBe("not_found");
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

describe("rota de dados da vitrine", () => {
  it("não manda `fetch` para a tela de login", () => {
    // Redirecionar devolveria HTML onde o navegador espera JSON. Quem decide o acesso é o
    // próprio handler, que responde 401/403 — e nunca um resultado parcial.
    expect(requiresSession("/api/loja/busca", "whitelist")).toBe(false);
    expect(requiresSession("/api/loja/busca", "login_required")).toBe(false);
  });

  it("e isso não abre a loja fechada para o resto", () => {
    expect(requiresSession("/loja", "whitelist")).toBe(true);
    expect(requiresSession("/carrinho", "whitelist")).toBe(true);
    expect(requiresSession("/api/loja", "whitelist"), "sem a barra não é a rota de dados").toBe(true);
    expect(requiresSession("/api/outra-coisa", "whitelist")).toBe(true);
  });
});
