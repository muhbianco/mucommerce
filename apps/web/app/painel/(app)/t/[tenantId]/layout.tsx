import Link from "next/link";
import type { ReactNode } from "react";

import { requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../panel.module.css";

// Menu entries follow the tenant's flags and the user's scopes; the API enforces both anyway.
export default async function TenantLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  const scopes = tenantScopes(me, context.tenant_id);
  const base = `/t/${encodeURIComponent(context.tenant_id)}`;
  const catalog = context.features.catalog && scopes.can("catalog:read");
  return (
    <>
      <h1>{context.name}</h1>
      <nav className={styles.subnav}>
        <Link href={base}>Visão geral</Link>
        {catalog ? <Link href={`${base}/produtos`}>Produtos</Link> : null}
        {catalog ? <Link href={`${base}/categorias`}>Categorias</Link> : null}
        {catalog && context.features.inventory && scopes.can("inventory:read") ? (
          <Link href={`${base}/estoque`}>Estoque</Link>
        ) : null}
        {scopes.can("customers:read") ? <Link href={`${base}/clientes`}>Clientes</Link> : null}
        {scopes.can("settings:write") && (context.features.checkout || context.features.pickup || context.features.delivery) ? (
          <Link href={`${base}/entrega`}>Entrega e checkout</Link>
        ) : null}
        {scopes.can("orders:read") && context.features.checkout ? <Link href={`${base}/pedidos`}>Pedidos</Link> : null}
        {scopes.can("payments:read") && context.features.checkout ? <Link href={`${base}/pagamentos`}>Pagamentos</Link> : null}
        {scopes.can("settings:write") ? <Link href={`${base}/configuracoes`}>Configurações</Link> : null}
      </nav>
      {children}
    </>
  );
}
