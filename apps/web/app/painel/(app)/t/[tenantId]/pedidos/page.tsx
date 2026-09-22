import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import { ORDER_STATUS_LABEL, type OrderSummary, type Page } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";

export const metadata: Metadata = { title: "Pedidos" };

const FILTERS = [
  ["", "Todos"],
  ["awaiting_payment", "Aguardando pagamento"],
  ["payment_confirmed", "Pagos"],
  ["accepted", "Aceitos"],
  ["in_production", "Em preparo"],
  ["ready_for_pickup", "Prontos"],
  ["shipped", "Em entrega"],
  ["delivered", "Entregues"],
  ["cancelled", "Cancelados"],
] as const;

function money(cents: number, currency: string): string {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency }).format(cents / 100);
}

export default async function Orders({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ status?: string; q?: string; cursor?: string; ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const { status = "", q = "", cursor, ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!context.features.checkout || !tenantScopes(me, context.tenant_id).can("orders:read")) notFound();
  const query = new URLSearchParams({ limit: "20" });
  if (status) query.set("status", status);
  if (q) query.set("q", q);
  if (cursor) query.set("cursor", cursor);
  const page = await api<Page<OrderSummary>>(`/admin/tenants/${tenantId}/orders?${query}`);
  const base = `/t/${tenantId}/pedidos`;
  const when = (iso: string) =>
    new Intl.DateTimeFormat("pt-BR", {
      dateStyle: "short",
      timeStyle: "short",
      timeZone: context.timezone,
    }).format(new Date(iso));

  return (
    <>
      <h2>Pedidos</h2>
      <Flash ok={ok} erro={erro} />
      <form method="get" className={styles.form}>
        <label>
          Situação
          <select name="status" defaultValue={status}>
            {FILTERS.map(([value, label]) => (
              <option key={value} value={value}>
                {label}
              </option>
            ))}
          </select>
        </label>
        <label>
          Buscar
          <input name="q" defaultValue={q} maxLength={60} placeholder="número ou nome do cliente" />
        </label>
        <button type="submit">Filtrar</button>
      </form>

      {page.items.length === 0 ? (
        <p className="muted">Nenhum pedido por aqui ainda.</p>
      ) : (
        <table className={styles.table}>
          <thead>
            <tr>
              <th scope="col">Pedido</th>
              <th scope="col">Cliente</th>
              <th scope="col">Situação</th>
              <th scope="col">Feito em</th>
              <th scope="col">Total</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map((order) => (
              <tr key={order.id}>
                <th scope="row">
                  <Link href={`${base}/${order.id}`}>#{order.number}</Link>
                </th>
                <td>{order.customer_name ?? "—"}</td>
                <td>
                  {ORDER_STATUS_LABEL[order.status] ?? order.status}
                  {order.refund_status !== "none" ? " · devolvido" : ""}
                </td>
                <td>{when(order.placed_at)}</td>
                <td>{money(order.total_cents, order.currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      {page.next_cursor ? (
        <p>
          <Link
            href={`${base}?${new URLSearchParams({ status, q, cursor: page.next_cursor }).toString()}`}
          >
            Ver mais
          </Link>
        </p>
      ) : null}
    </>
  );
}
