import { randomUUID } from "node:crypto";

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, ApiError, requireMe } from "@/lib/panel/api";
import { formatQuantity } from "@/lib/panel/format";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import { type Movement, MOVEMENT_LABEL, type Page } from "@/lib/panel/types";

import styles from "../../../../../panel.module.css";
import { adjustStock, setMinLevel } from "../../actions";
import { Flash } from "../../flash";

export const metadata: Metadata = { title: "Extrato de estoque" };

export default async function StockStatement({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string; variantId: string }>;
  searchParams: Promise<{ cursor?: string; ok?: string; erro?: string }>;
}) {
  const { tenantId, variantId } = await params;
  const query = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!context.features.catalog || !context.features.inventory) notFound();
  const canAdjust = tenantScopes(me, context.tenant_id).can("inventory:adjust");
  const base = `/t/${encodeURIComponent(context.tenant_id)}`;
  const search = new URLSearchParams({ limit: "50" });
  if (query.cursor) search.set("cursor", query.cursor.slice(0, 256));

  let page: Page<Movement>;
  try {
    page = await api<Page<Movement>>(
      `/admin/tenants/${context.tenant_id}/inventory/variants/${encodeURIComponent(variantId)}/movements?${search.toString()}`,
    );
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) notFound();
    throw error;
  }
  const hidden = (
    <>
      <input type="hidden" name="tenant_id" value={context.tenant_id} />
      <input type="hidden" name="variant_id" value={variantId} />
    </>
  );

  return (
    <>
      <p>
        <Link href={`${base}/estoque`}>← Estoque</Link>
      </p>
      <Flash ok={query.ok} erro={query.erro} />
      {canAdjust ? (
        <section className={styles.card}>
          <h2>Movimentar</h2>
          <form action={adjustStock} className={styles.form}>
            {hidden}
            <input type="hidden" name="back" value="extrato" />
            <input type="hidden" name="idempotency_key" value={randomUUID()} />
            <label>
              Tipo
              <select name="kind" defaultValue="receipt">
                <option value="receipt">Entrada</option>
                <option value="loss">Perda</option>
                <option value="count">Contagem</option>
                <option value="adjustment">Ajuste (±)</option>
              </select>
            </label>
            <label>
              Quantidade
              <input name="quantity" required inputMode="decimal" style={{ width: "6rem" }} />
            </label>
            <label>
              Custo unitário (entradas)
              <input name="unit_cost" inputMode="decimal" placeholder="R$" style={{ width: "7rem" }} />
            </label>
            <label>
              Motivo
              <input name="reason" maxLength={200} />
            </label>
            <button type="submit" className={styles.button}>
              Registrar
            </button>
          </form>
          <form action={setMinLevel} className={styles.form} style={{ marginTop: "1rem" }}>
            {hidden}
            <label>
              Alerta de estoque baixo abaixo de
              <input name="min_level" inputMode="decimal" style={{ width: "6rem" }} placeholder="vazio = sem alerta" />
            </label>
            <button type="submit" className={styles.buttonGhost}>
              Salvar mínimo
            </button>
          </form>
        </section>
      ) : null}
      <section className={styles.card}>
        <h2>Extrato</h2>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Quando</th>
              <th>Tipo</th>
              <th>Quantidade</th>
              <th>Saldo</th>
              <th>Motivo</th>
              <th>Por</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map((m) => (
              <tr key={m.id}>
                <td>{new Date(m.occurred_at).toLocaleString("pt-BR", { timeZone: context.timezone })}</td>
                <td>{MOVEMENT_LABEL[m.movement_type] ?? m.movement_type}</td>
                <td>{formatQuantity(m.quantity, "")}</td>
                <td>{formatQuantity(m.balance_after, "")}</td>
                <td>{m.reason ?? "—"}</td>
                <td>
                  <small>{m.actor}</small>
                </td>
              </tr>
            ))}
            {page.items.length === 0 ? (
              <tr>
                <td colSpan={6}>Sem movimentos ainda.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
        {page.next_cursor ? (
          <p>
            <Link href={`${base}/estoque/${encodeURIComponent(variantId)}?cursor=${encodeURIComponent(page.next_cursor)}`}>
              Mais antigos →
            </Link>
          </p>
        ) : null}
      </section>
    </>
  );
}
