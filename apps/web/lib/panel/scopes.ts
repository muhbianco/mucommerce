import type { Me } from "./types";

/** Scopes the signed-in user holds on a tenant. Platform staff pass every tenant check. */
export function tenantScopes(me: Me, tenantId: string): { can: (scope: string) => boolean } {
  if (me.platform_role) return { can: () => true };
  const membership = me.memberships.find((m) => m.tenant_id === tenantId);
  const scopes = new Set(membership?.scopes ?? []);
  return { can: (scope) => scopes.has(scope) };
}
