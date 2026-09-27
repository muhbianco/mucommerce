import type { ReactNode } from "react";

import { requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../panel.module.css";
import { type NavItem, PanelNav } from "./panel-nav";

/** Assinatura em atraso: o lojista precisa saber antes de a vitrine sair do ar. */
function BillingNotice({ status, graceUntil }: { status: string; graceUntil: string | null }) {
  if (status !== "suspended") return null;
  const deadline = graceUntil ? new Date(`${graceUntil}Z`) : null;
  const inGrace = deadline !== null && deadline.getTime() > Date.now();
  const when = deadline
    ? new Intl.DateTimeFormat("pt-BR", {
        dateStyle: "short",
        timeStyle: "short",
        timeZone: "America/Sao_Paulo",
      }).format(deadline)
    : null;
  return (
    <p className={styles.error}>
      {inGrace
        ? `Assinatura em atraso: a sua vitrine sai do ar em ${when}. `
        : "A sua vitrine está fora do ar por falta de pagamento. "}
      Adicione saldo na sua conta MuhBianco para manter a loja vendendo — o painel e os seus dados
      continuam aqui.{" "}
      <a href="https://muhbianco.com.br/conta.html#saldo" target="_blank" rel="noopener">
        Adicionar saldo
      </a>
    </p>
  );
}

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
  const f = context.features;
  const entries: (NavItem | false | undefined)[] = [
    { href: base, label: "Visão geral" },
    catalog && { href: `${base}/produtos`, label: "Produtos" },
    catalog && { href: `${base}/categorias`, label: "Categorias" },
    catalog && f.inventory && scopes.can("inventory:read") && { href: `${base}/estoque`, label: "Estoque" },
    scopes.can("customers:read") && { href: `${base}/clientes`, label: "Clientes" },
    scopes.can("settings:write") &&
      (f.checkout || f.pickup || f.delivery) && { href: `${base}/entrega`, label: "Entrega e checkout" },
    scopes.can("orders:read") && f.checkout && { href: `${base}/pedidos`, label: "Pedidos" },
    scopes.can("settings:write") && f.coupons && { href: `${base}/cupons`, label: "Cupons" },
    scopes.can("payments:read") && f.checkout && { href: `${base}/pagamentos`, label: "Pagamentos" },
    scopes.can("domains:write") && { href: `${base}/dominios`, label: "Endereços" },
    scopes.can("settings:write") && { href: `${base}/configuracoes`, label: "Configurações" },
  ];
  const items = entries.filter((item): item is NavItem => Boolean(item));
  const live = context.status === "active";
  return (
    <>
      <header className={styles.storeHead}>
        <h1>{context.name}</h1>
        <span className={styles.pill} data-state={live ? "live" : context.status === "suspended" ? "warn" : "off"}>
          {live ? "Loja no ar" : context.status === "suspended" ? "Assinatura em atraso" : context.status}
        </span>
        {context.primary_host ? (
          <a
            className={`${styles.buttonGhost} ${styles.buttonSmall} ${styles.storeHeadLink}`}
            href={`https://${context.primary_host}`}
            target="_blank"
            rel="noopener"
          >
            Ver a loja ↗
          </a>
        ) : null}
      </header>
      <BillingNotice status={context.status} graceUntil={context.billing_grace_until} />
      <PanelNav items={items} base={base} />
      {children}
    </>
  );
}
