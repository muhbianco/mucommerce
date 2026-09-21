import { randomUUID } from "node:crypto";

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { formatQuantity } from "@/lib/panel/format";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Balance, Page } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { adjustStock } from "../actions";
import { Flash } from "../flash";

export const metadata: Metadata = { title: "Estoque" };

export default async function Stock({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ q?: string; baixo?: string; cursor?: string; ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const query = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!context.features.catalog || !context.features.inventory) notFound();
  const canAdjust = tenantScopes(me, context.tenant_id).can("inventory:adjust");
  const base = `/t/${encodeURIComponent(context.tenant_id)}`;

  const search = new URLSearchParams({ limit: "50" });
  if (query.q) search.set("q", query.q.slice(0, 100));
  if (query.baixo === "1") search.set("low_stock", "true");
  if (query.cursor) search.set("cursor", query.cursor.slice(0, 256));
  const page = await api<Page<Balance>>(
    `/admin/tenants/${context.tenant_id}/inventory/balances?${search.toString()}`,
  );
  const next = new URLSearchParams();
  if (query.q) next.set("q", query.q);
  if (query.baixo === "1") next.set("baixo", "1");
  if (page.next_cursor) next.set("cursor", page.next_cursor);

  return (
    <>
      <Flash ok={query.ok} erro={query.erro} />
      <section className={styles.card}>
        <h2>Estoque</h2>
        <form className={styles.form} method="get">
          <label>
            Buscar
            <input name="q" defaultValue={query.q ?? ""} placeholder="produto ou SKU" />
          </label>
          <label>
            <span>
              <input type="checkbox" name="baixo" value="1" defaultChecked={query.baixo === "1"} /> só estoque baixo
            </span>
          </label>
          <button type="submit" className={styles.buttonGhost}>
            Filtrar
          </button>
        </form>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Produto</th>
              <th>Disponível</th>
              <th>Mínimo</th>
              {canAdjust ? <th>Movimentar</th> : null}
            </tr>
          </thead>
          <tbody>
            {page.items.map((row) => (
              <tr key={row.variant_id}>
                <td>
                  <Link href={`${base}/estoque/${row.variant_id}`}>{row.product_name}</Link>
                  <br />
                  <small>{row.sku}</small>
                </td>
                <td>
                  {formatQuantity(row.available, row.unit_label)}
                  {row.low_stock ? <span className={styles.badge}> baixo</span> : null}
                </td>
                <td>{row.min_level ? formatQuantity(row.min_level, row.unit_label) : "—"}</td>
                {canAdjust ? (
                  <td>
                    <form action={adjustStock} className={styles.form}>
                      <input type="hidden" name="tenant_id" value={context.tenant_id} />
                      <input type="hidden" name="variant_id" value={row.variant_id} />
                      <input type="hidden" name="idempotency_key" value={randomUUID()} />
                      <select name="kind" defaultValue="receipt">
                        <option value="receipt">Entrada</option>
                        <option value="loss">Perda</option>
                        <option value="count">Contagem</option>
                        <option value="adjustment">Ajuste (±)</option>
                      </select>
                      <input name="quantity" required inputMode="decimal" placeholder="qtd" style={{ width: "5rem" }} />
                      <input name="reason" maxLength={200} placeholder="motivo" style={{ width: "9rem" }} />
                      <button type="submit" className={styles.buttonGhost}>
                        OK
                      </button>
                    </form>
                  </td>
                ) : null}
              </tr>
            ))}
            {page.items.length === 0 ? (
              <tr>
                <td colSpan={4}>Nenhum produto com estoque controlado.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
        {page.next_cursor ? (
          <p>
            <Link href={`${base}/estoque?${next.toString()}`}>Próxima página →</Link>
          </p>
        ) : null}
        <p className="muted">Perda e ajuste pedem motivo. Contagem informa o total contado.</p>
      </section>
    </>
  );
}
