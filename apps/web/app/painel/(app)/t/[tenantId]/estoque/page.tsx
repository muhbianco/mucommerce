import { randomUUID } from "node:crypto";

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { formatQuantity } from "@/lib/panel/format";
import { tenantScopes } from "@/lib/panel/scopes";
import type { PillState } from "@/lib/panel/states";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Balance, Page } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { adjustStock } from "../actions";
import { Flash } from "../flash";
import { EmptyState, PageHeader, Pill, Section, Stat, Stats, TableWrap } from "../ui";
import local from "./estoque.module.css";

export const metadata: Metadata = { title: "Estoque" };

// Variante única do produto: o nome não diz nada ao lojista, então não aparece.
const DEFAULT_VARIANT = "Padrão";

/** Esgotado vence "baixo": sem nada para vender é o problema maior. */
function stockState(row: Balance): { state: PillState; label: string } {
  if (Number(row.available) <= 0) return { state: "warn", label: "Esgotado" };
  if (row.low_stock) return { state: "warn", label: "Estoque baixo" };
  return { state: "live", label: "Em estoque" };
}

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
  // Mesmos filtros, sem cursor: volta para a primeira página.
  const firstHref = `${base}/estoque${next.toString() ? `?${next.toString()}` : ""}`;
  if (page.next_cursor) next.set("cursor", page.next_cursor);

  const rows = page.items;
  const filtered = Boolean(query.q) || query.baixo === "1";
  const soldOut = rows.filter((row) => Number(row.available) <= 0).length;
  const low = rows.filter((row) => row.low_stock).length;

  return (
    <>
      <PageHeader
        eyebrow="Estoque"
        title="Quanto você tem de cada produto"
        lead={
          canAdjust
            ? "Veja o que está acabando e registre entradas, perdas e contagens direto na lista."
            : "Veja o que está acabando e abra um produto para ver tudo o que entrou e saiu."
        }
      />
      <Flash ok={query.ok} erro={query.erro} />

      {rows.length ? (
        <Stats>
          <Stat
            label={query.cursor || page.next_cursor ? "Nesta página" : "Na lista"}
            value={rows.length}
            hint={page.next_cursor ? "tem mais na próxima página" : "itens com estoque controlado (cada variante conta)"}
          />
          <Stat label="Esgotados" value={soldOut} hint="nada disponível para vender" />
          <Stat
            label="Estoque baixo"
            value={low}
            hint={low && query.baixo !== "1" ? "abaixo do mínimo · ver só esses" : "abaixo do mínimo que você definiu"}
            href={low && query.baixo !== "1" ? `${base}/estoque?baixo=1` : undefined}
          />
        </Stats>
      ) : null}

      <Section title="Produtos" description="Clique no nome para ver o extrato completo">
        <form className={styles.toolbar} method="get">
          <label className={local.search}>
            Buscar
            <input type="search" name="q" defaultValue={query.q ?? ""} placeholder="Nome do produto ou SKU" />
          </label>
          <label>
            <span className={local.checkInline}>
              <input type="checkbox" name="baixo" value="1" defaultChecked={query.baixo === "1"} /> Só estoque baixo
            </span>
          </label>
          <button type="submit" className={styles.buttonGhost}>
            Filtrar
          </button>
          {filtered ? (
            <Link href={`${base}/estoque`} className={local.clear}>
              Limpar filtros
            </Link>
          ) : null}
        </form>

        {rows.length === 0 ? (
          filtered ? (
            <EmptyState title="Nada encontrado">
              Nenhum produto com estoque controlado bate com esse filtro. Tente outra busca ou limpe os filtros.
            </EmptyState>
          ) : (
            <EmptyState
              title="Nenhum produto com estoque controlado"
              action={
                <Link href={`${base}/produtos`} className={styles.buttonGhost}>
                  Ir para Produtos
                </Link>
              }
            >
              Abra um produto e, no campo Estoque, escolha &ldquo;Controlar estoque&rdquo;. Ele passa a
              aparecer aqui.
            </EmptyState>
          )
        ) : (
          <TableWrap>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Produto</th>
                  <th>Situação</th>
                  <th className={styles.num}>Disponível</th>
                  <th className={styles.num}>Mínimo</th>
                  {canAdjust ? <th>Registrar movimento</th> : null}
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const view = stockState(row);
                  const variant = row.variant_name && row.variant_name !== DEFAULT_VARIANT ? row.variant_name : null;
                  return (
                    <tr key={row.variant_id} className={view.state === "warn" ? local.alert : undefined}>
                      <td>
                        <Link href={`${base}/estoque/${row.variant_id}`} className={local.name}>
                          {row.product_name}
                        </Link>
                        <span className={local.sub}>
                          {variant ? `${variant} · ` : ""}SKU {row.sku}
                        </span>
                      </td>
                      <td>
                        <Pill state={view.state}>{view.label}</Pill>
                      </td>
                      <td className={styles.num}>
                        <strong>{formatQuantity(row.available, row.unit_label)}</strong>
                        {Number(row.reserved) > 0 ? (
                          <span className={local.sub}>
                            + {formatQuantity(row.reserved, row.unit_label)} em pedidos
                          </span>
                        ) : null}
                      </td>
                      <td className={styles.num}>
                        {row.min_level ? formatQuantity(row.min_level, row.unit_label) : "—"}
                      </td>
                      {canAdjust ? (
                        <td>
                          <form action={adjustStock} className={local.quick}>
                            <input type="hidden" name="tenant_id" value={context.tenant_id} />
                            <input type="hidden" name="variant_id" value={row.variant_id} />
                            <input type="hidden" name="idempotency_key" value={randomUUID()} />
                            <select name="kind" defaultValue="receipt" aria-label="Tipo de movimento">
                              <option value="receipt">Entrada</option>
                              <option value="loss">Perda</option>
                              <option value="count">Contagem</option>
                              <option value="adjustment">Ajuste (±)</option>
                            </select>
                            <input
                              name="quantity"
                              required
                              inputMode="decimal"
                              placeholder="Qtd."
                              aria-label="Quantidade"
                              className={local.qty}
                            />
                            <input
                              name="reason"
                              maxLength={200}
                              placeholder="Motivo"
                              aria-label="Motivo"
                              className={local.reason}
                            />
                            <button type="submit" className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
                              Registrar
                            </button>
                          </form>
                        </td>
                      ) : null}
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
        )}

        {query.cursor || page.next_cursor ? (
          <div className={styles.pager}>
            {query.cursor ? (
              <Link href={firstHref} className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
                ← Primeira página
              </Link>
            ) : null}
            {page.next_cursor ? (
              <Link href={`${base}/estoque?${next.toString()}`} className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
                Próxima página →
              </Link>
            ) : null}
          </div>
        ) : null}

        {canAdjust && rows.length ? (
          <p className={styles.hint}>
            Entrada soma e perda tira. Na contagem, informe o total que você contou. Ajuste aceita sinal de
            menos para tirar. Perda e ajuste pedem motivo.
          </p>
        ) : null}
      </Section>
    </>
  );
}
