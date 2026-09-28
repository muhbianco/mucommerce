import { randomUUID } from "node:crypto";

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { formatMoney } from "@/lib/panel/format";
import { tenantScopes } from "@/lib/panel/scopes";
import { PRODUCT_STATE, stateOf } from "@/lib/panel/states";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import { type Page, PRODUCT_KINDS, PRODUCT_STATUS_LABEL, type ProductSummary } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { createProduct } from "../actions";
import { Flash } from "../flash";
import { EmptyState, PageHeader, Pill, Section, TableWrap } from "../ui";
import local from "./produtos.module.css";

export const metadata: Metadata = { title: "Produtos" };

const STATUSES = ["", "draft", "active", "paused", "inactive", "archived"];

/** "rascunho" → "Rascunho": as opções do filtro começam com maiúscula. */
function capitalize(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

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
  const canWrite = scopes.can("catalog:write");

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
  // A paginação por cursor só anda para a frente: este link volta ao começo com os mesmos filtros.
  const first = new URLSearchParams(search);
  first.delete("limit");
  first.delete("cursor");
  const firstQuery = first.toString();
  const filtered = search.has("q") || search.has("status");
  const pagerButton = `${styles.buttonGhost} ${styles.buttonSmall}`;

  return (
    <>
      <PageHeader
        eyebrow="Produtos"
        title="O que a sua loja vende"
        lead="Crie o produto com nome e preço e complete fotos e detalhes na página dele. Na loja só aparece o que você publicar."
      />
      <Flash ok={query.ok} erro={query.erro} />

      {canWrite ? (
        <Section title="Novo produto" description="Nome e preço bastam para começar">
          <form action={createProduct}>
            <input type="hidden" name="tenant_id" value={context.tenant_id} />
            <input type="hidden" name="idempotency_key" value={randomUUID()} />
            <div className={styles.fields}>
              <label className={styles.field}>
                Nome
                <input name="name" required maxLength={200} placeholder="ex.: Bolo de cenoura" />
              </label>
              <label className={styles.field}>
                Preço (R$)
                <input name="price" required inputMode="decimal" placeholder="12,50" />
              </label>
              <label className={styles.field}>
                Código (SKU)
                <input name="sku" maxLength={64} placeholder="Gerado se ficar vazio" />
                <span className={styles.fieldHint}>Opcional: use se você já tem um código seu.</span>
              </label>
            </div>
            <div className={styles.formActions}>
              <span className={local.actionsHint}>Ele nasce como rascunho: ninguém vê até você publicar.</span>
              <button type="submit" className={styles.button}>
                Criar produto
              </button>
            </div>
          </form>
        </Section>
      ) : null}

      <Section title="Seus produtos" description="Clique no nome para editar">
        <form className={styles.toolbar} method="get">
          <label className={local.search}>
            Buscar
            <input name="q" type="search" defaultValue={query.q ?? ""} placeholder="Nome ou SKU" />
          </label>
          <label>
            Situação
            <select name="status" defaultValue={query.status ?? ""}>
              {STATUSES.map((status) => (
                <option key={status} value={status}>
                  {status ? capitalize(PRODUCT_STATUS_LABEL[status] ?? status) : "Ativos e rascunhos"}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className={styles.buttonGhost}>
            Filtrar
          </button>
          {filtered ? (
            <Link href={`${base}/produtos`} className={local.clear}>
              Limpar filtros
            </Link>
          ) : null}
        </form>

        {page.items.length === 0 ? (
          <EmptyState title={filtered || query.cursor ? "Nenhum produto encontrado" : "Nenhum produto ainda"}>
            {filtered || query.cursor
              ? "Tente outra busca ou outra situação."
              : canWrite
                ? "Crie o primeiro no formulário acima: nome e preço bastam."
                : "Quando a loja tiver produtos, eles aparecem aqui."}
          </EmptyState>
        ) : (
          <TableWrap>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>
                    <span className={local.srOnly}>Foto</span>
                  </th>
                  <th>Produto</th>
                  <th className={local.hideSm}>SKU</th>
                  <th className={styles.num}>Preço</th>
                  <th>Situação</th>
                </tr>
              </thead>
              <tbody>
                {page.items.map((product) => (
                  <tr key={product.id}>
                    <td className={local.thumbCell}>
                      {product.cover_url ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img className={styles.thumb} src={product.cover_url} alt="" width={44} height={44} />
                      ) : (
                        <span className={styles.thumb} aria-hidden="true" />
                      )}
                    </td>
                    <td>
                      <Link href={`${base}/produtos/${product.id}`} className={local.nameLink}>
                        {product.name}
                      </Link>
                      {product.kind !== "physical" && PRODUCT_KINDS[product.kind] ? (
                        <span className={local.sub}>{PRODUCT_KINDS[product.kind]}</span>
                      ) : null}
                    </td>
                    <td className={`${local.sku} ${local.hideSm}`}>{product.sku}</td>
                    <td className={styles.num}>
                      {/* Ingresso vende pelo preço de cada lote, que fica na página do produto. */}
                      {product.kind === "ticket" ? (
                        <span className="muted">por lote</span>
                      ) : (
                        formatMoney(product.price.amount_cents)
                      )}
                      {product.kind !== "ticket" && product.price.promo_active ? (
                        <span className={local.promo}>
                          Promoção
                          {product.price.compare_at_cents ? (
                            <>
                              {" "}
                              · de <s>{formatMoney(product.price.compare_at_cents)}</s>
                            </>
                          ) : null}
                        </span>
                      ) : null}
                    </td>
                    <td>
                      <Pill state={stateOf(PRODUCT_STATE, product.status)}>
                        {PRODUCT_STATUS_LABEL[product.status] ?? product.status}
                      </Pill>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}

        {query.cursor || page.next_cursor ? (
          <div className={styles.pager}>
            {query.cursor ? (
              <Link href={`${base}/produtos${firstQuery ? `?${firstQuery}` : ""}`} className={pagerButton}>
                ← Primeira página
              </Link>
            ) : null}
            {page.next_cursor ? (
              <Link href={`${base}/produtos?${next.toString()}`} className={pagerButton}>
                Próxima página →
              </Link>
            ) : null}
          </div>
        ) : null}
      </Section>
    </>
  );
}
