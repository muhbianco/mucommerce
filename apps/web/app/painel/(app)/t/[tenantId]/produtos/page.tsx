import { randomUUID } from "node:crypto";

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { formatMoney } from "@/lib/panel/format";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import { type Page, PRODUCT_STATUS_LABEL, type ProductSummary } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { createProduct } from "../actions";
import { Flash } from "../flash";

export const metadata: Metadata = { title: "Produtos" };

const STATUSES = ["", "draft", "active", "inactive", "archived"];

export default async function Products({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ q?: string; status?: string; cursor?: string; ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const query = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!context.features.catalog) notFound();
  const scopes = tenantScopes(me, context.tenant_id);
  const base = `/t/${encodeURIComponent(context.tenant_id)}`;

  const search = new URLSearchParams({ limit: "25" });
  if (query.q) search.set("q", query.q.slice(0, 100));
  if (query.status && STATUSES.includes(query.status)) search.set("status", query.status);
  if (query.cursor) search.set("cursor", query.cursor.slice(0, 256));
  const page = await api<Page<ProductSummary>>(
    `/admin/tenants/${context.tenant_id}/products?${search.toString()}`,
  );
  const next = new URLSearchParams(search);
  next.delete("limit");
  if (page.next_cursor) next.set("cursor", page.next_cursor);

  return (
    <>
      <Flash ok={query.ok} erro={query.erro} />
      {scopes.can("catalog:write") ? (
        <section className={styles.card}>
          <h2>Novo produto</h2>
          <form action={createProduct} className={styles.form}>
            <input type="hidden" name="tenant_id" value={context.tenant_id} />
            <input type="hidden" name="idempotency_key" value={randomUUID()} />
            <label>
              Nome
              <input name="name" required maxLength={200} />
            </label>
            <label>
              Preço (R$)
              <input name="price" required inputMode="decimal" placeholder="12,50" />
            </label>
            <label>
              SKU (opcional)
              <input name="sku" maxLength={64} placeholder="gerado se vazio" />
            </label>
            <button type="submit" className={styles.button}>
              Criar rascunho
            </button>
          </form>
        </section>
      ) : null}

      <section className={styles.card}>
        <h2>Produtos</h2>
        <form className={styles.form} method="get">
          <label>
            Buscar
            <input name="q" defaultValue={query.q ?? ""} placeholder="nome ou SKU" />
          </label>
          <label>
            Status
            <select name="status" defaultValue={query.status ?? ""}>
              {STATUSES.map((status) => (
                <option key={status} value={status}>
                  {status ? PRODUCT_STATUS_LABEL[status] : "ativos e rascunhos"}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className={styles.buttonGhost}>
            Filtrar
          </button>
        </form>
        <table className={styles.table}>
          <thead>
            <tr>
              <th />
              <th>Produto</th>
              <th>SKU</th>
              <th>Preço</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map((product) => (
              <tr key={product.id}>
                <td>
                  {product.cover_url ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={product.cover_url} alt="" width={48} height={48} style={{ objectFit: "cover" }} />
                  ) : null}
                </td>
                <td>
                  <Link href={`${base}/produtos/${product.id}`}>{product.name}</Link>
                </td>
                <td>{product.sku}</td>
                <td>
                  {formatMoney(product.price.amount_cents)}
                  {product.price.promo_active ? " (promo)" : ""}
                </td>
                <td>
                  <span className={styles.badge}>{PRODUCT_STATUS_LABEL[product.status]}</span>
                </td>
              </tr>
            ))}
            {page.items.length === 0 ? (
              <tr>
                <td colSpan={5}>Nenhum produto.</td>
              </tr>
            ) : null}
          </tbody>
        </table>
        {page.next_cursor ? (
          <p>
            <Link href={`${base}/produtos?${next.toString()}`}>Próxima página →</Link>
          </p>
        ) : null}
      </section>
    </>
  );
}
