import type { Me } from "./types";

/** Scopes the signed-in user holds on a tenant. Platform staff pass every tenant check. */
export function tenantScopes(me: Me, tenantId: string): { can: (scope: string) => boolean } {
  if (me.platform_role) return { can: () => true };
  const membership = me.memberships.find((m) => m.tenant_id === tenantId);
  const scopes = new Set(membership?.scopes ?? []);
  return { can: (scope) => scopes.has(scope) };
}

/**
 * A pessoa é da equipe desta loja?
 *
 * Diferente de `tenantScopes`: o staff da MuhBianco passa em tudo que é leitura, mas as telas
 * onde a decisão custa dinheiro (ligar módulo, credencial de pagamento, comprar etiqueta) são
 * `members_only` no servidor. Sem esta distinção a tela oferece um botão que vai dar 403.
 */
export function isStoreMember(me: Me, tenantId: string): boolean {
  return me.memberships.some((m) => m.tenant_id === tenantId);
}
