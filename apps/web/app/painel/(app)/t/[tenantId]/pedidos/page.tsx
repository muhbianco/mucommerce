import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { ORDER_STATE, stateOf } from "@/lib/panel/states";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import { ORDER_STATUS_LABEL, type OrderSummary, type Page } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { EmptyState, PageHeader, Pill, Section, TableWrap } from "../ui";

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

  const filtered = Boolean(status || q);
  const filterLabel = FILTERS.find(([value]) => value === status)?.[1];
  const count = page.items.length;
  // Link to the first page with the same filters (only shown after "Próximos pedidos").
  const firstPage = filtered ? `${base}?${new URLSearchParams({ status, q }).toString()}` : base;

  return (
    <>
      <PageHeader
        eyebrow="Pedidos"
        title="O que os clientes compraram"
        lead="Abra um pedido para aceitar, avisar que está pronto ou devolver o dinheiro. Os mais novos aparecem primeiro."
      />
      <Flash ok={ok} erro={erro} />

      <Section
        title={status && filterLabel ? filterLabel : "Todos os pedidos"}
        description={
          count ? `${count} ${count === 1 ? "pedido" : "pedidos"}${page.next_cursor ? " nesta página" : ""}` : undefined
        }
      >
        <form method="get" className={styles.toolbar}>
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
          <label className={styles.formGrow}>
            Buscar
            <input name="q" defaultValue={q} maxLength={60} placeholder="número ou nome do cliente" />
          </label>
          <button type="submit" className={styles.buttonGhost}>
            Filtrar
          </button>
          {filtered ? (
            // buttonGhost gives the button shape to the link; buttonDanger keeps it discreet.
            <Link href={base} className={`${styles.buttonGhost} ${styles.buttonDanger}`}>
              Limpar filtros
            </Link>
          ) : null}
        </form>

        {count === 0 ? (
          filtered ? (
            <EmptyState title="Nenhum pedido com esse filtro">
              Tente outra situação ou apague a busca para ver todos os pedidos.
            </EmptyState>
          ) : (
            <EmptyState title="Nenhum pedido por aqui ainda">
              Quando alguém comprar na sua loja, o pedido aparece aqui na hora.
            </EmptyState>
          )
        ) : (
          <TableWrap>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th scope="col">Pedido</th>
                  <th scope="col">Cliente</th>
                  <th scope="col">Situação</th>
                  <th scope="col">Feito em</th>
                  <th scope="col" className={styles.num}>
                    Total
                  </th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((order) => (
                  <tr key={order.id}>
                    <td>
                      <Link href={`${base}/${order.id}`}>
                        <strong>#{order.number}</strong>
                      </Link>
                    </td>
                    <td>{order.customer_name ?? "—"}</td>
                    <td>
                      <span className={styles.rowBadges}>
                        <Pill state={stateOf(ORDER_STATE, order.status)}>
                          {ORDER_STATUS_LABEL[order.status] ?? order.status}
                        </Pill>
                        {order.refund_status !== "none" ? <Pill state="info">Devolvido</Pill> : null}
                      </span>
                    </td>
                    <td>{when(order.placed_at)}</td>
                    <td className={styles.num}>{money(order.total_cents, order.currency)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}

        {cursor || page.next_cursor ? (
          <div className={styles.pager}>
            {cursor ? (
              <Link href={firstPage} className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
                ← Mais recentes
              </Link>
            ) : null}
            {page.next_cursor ? (
              <Link
                href={`${base}?${new URLSearchParams({ status, q, cursor: page.next_cursor }).toString()}`}
                className={`${styles.buttonGhost} ${styles.buttonSmall}`}
              >
                Ver mais →
              </Link>
            ) : null}
          </div>
        ) : null}
      </Section>
    </>
  );
}
