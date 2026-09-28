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
import { EmptyState, KeyValues, PageHeader, Section, TableWrap } from "../../ui";
import local from "../estoque.module.css";

export const metadata: Metadata = { title: "Extrato de estoque" };

/** Quantidade com sinal: "+5" entrou, "-2" saiu. */
function signed(quantity: string): string {
  const text = formatQuantity(quantity, "").trim();
  return Number(quantity) > 0 ? `+${text}` : text;
}

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
  const when = (iso: string) =>
    new Date(iso).toLocaleString("pt-BR", { timeZone: context.timezone, dateStyle: "short", timeStyle: "short" });
  // A lista vem do mais recente para o mais antigo: na primeira página, o topo é o saldo de agora.
  const latest = query.cursor ? undefined : page.items[0];
  const selfHref = `${base}/estoque/${encodeURIComponent(variantId)}`;
  const hasAside = Boolean(latest) || canAdjust;

  return (
    <>
      <p className={local.back}>
        <Link href={`${base}/estoque`}>← Voltar ao estoque</Link>
      </p>
      <PageHeader
        eyebrow="Estoque"
        title="Extrato do produto"
        lead={
          canAdjust
            ? "Tudo o que entrou e saiu deste item, do mais recente para o mais antigo. Registre um movimento ou ajuste o alerta de estoque baixo."
            : "Tudo o que entrou e saiu deste item, do mais recente para o mais antigo."
        }
      />
      <Flash ok={query.ok} erro={query.erro} />

      <div className={hasAside ? styles.split : undefined}>
        <Section title="Movimentos" description={query.cursor ? "Movimentos mais antigos" : undefined}>
          {page.items.length === 0 ? (
            <EmptyState title="Nenhum movimento ainda">
              Quando você registrar uma entrada ou vender este item, o movimento aparece aqui.
            </EmptyState>
          ) : (
            <TableWrap>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th>Quando</th>
                    <th>Tipo</th>
                    <th className={styles.num}>Quantidade</th>
                    <th className={styles.num}>Saldo</th>
                    <th>Motivo</th>
                    <th>Por</th>
                  </tr>
                </thead>
                <tbody>
                  {page.items.map((m) => (
                    <tr key={m.id}>
                      <td className={local.when}>{when(m.occurred_at)}</td>
                      <td>{MOVEMENT_LABEL[m.movement_type] ?? m.movement_type}</td>
                      <td className={styles.num}>
                        <strong>{signed(m.quantity)}</strong>
                      </td>
                      <td className={styles.num}>{formatQuantity(m.balance_after, "")}</td>
                      <td>{m.reason ?? "—"}</td>
                      <td className={local.actor}>{m.actor}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </TableWrap>
          )}
          {query.cursor || page.next_cursor ? (
            <div className={styles.pager}>
              {query.cursor ? (
                <Link href={selfHref} className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
                  ← Mais recentes
                </Link>
              ) : null}
              {page.next_cursor ? (
                <Link
                  href={`${selfHref}?cursor=${encodeURIComponent(page.next_cursor)}`}
                  className={`${styles.buttonGhost} ${styles.buttonSmall}`}
                >
                  Mais antigos →
                </Link>
              ) : null}
            </div>
          ) : null}
        </Section>

        {hasAside ? (
          <div className={local.aside}>
            {latest ? (
              <Section title="Saldo agora">
                <KeyValues
                  items={[
                    { label: "Em estoque", value: <strong>{formatQuantity(latest.balance_after, "")}</strong> },
                    {
                      label: "Último movimento",
                      value: `${MOVEMENT_LABEL[latest.movement_type] ?? latest.movement_type} em ${when(latest.occurred_at)}`,
                    },
                  ]}
                />
                <p className={styles.hint}>Inclui o que está reservado em pedidos que ainda não saíram.</p>
              </Section>
            ) : null}
  
            {canAdjust ? (
              <Section title="Registrar movimento">
                <form action={adjustStock}>
                  {hidden}
                  <input type="hidden" name="back" value="extrato" />
                  <input type="hidden" name="idempotency_key" value={randomUUID()} />
                  <div className={styles.fields}>
                    <label className={styles.field}>
                      Tipo
                      <select name="kind" defaultValue="receipt">
                        <option value="receipt">Entrada</option>
                        <option value="loss">Perda</option>
                        <option value="count">Contagem</option>
                        <option value="adjustment">Ajuste (±)</option>
                      </select>
                      <span className={styles.fieldHint}>
                        Entrada soma, perda tira. Contagem troca o saldo pelo total contado.
                      </span>
                    </label>
                    <label className={styles.field}>
                      Quantidade
                      <input name="quantity" required inputMode="decimal" />
                      <span className={styles.fieldHint}>No ajuste, use sinal de menos para tirar.</span>
                    </label>
                    <label className={styles.field}>
                      Custo unitário (R$)
                      <input name="unit_cost" inputMode="decimal" placeholder="Opcional" />
                      <span className={styles.fieldHint}>Só vale para entradas.</span>
                    </label>
                    <label className={styles.field}>
                      Motivo
                      <input name="reason" maxLength={200} />
                      <span className={styles.fieldHint}>Obrigatório em perda e ajuste.</span>
                    </label>
                  </div>
                  <div className={styles.formActions}>
                    <button type="submit" className={styles.button}>
                      Registrar
                    </button>
                  </div>
                </form>
              </Section>
            ) : null}
  
            {canAdjust ? (
              <Section title="Alerta de estoque baixo">
                <form action={setMinLevel}>
                  {hidden}
                  <div className={styles.fields}>
                    <label className={styles.field}>
                      Avisar quando ficar abaixo de
                      <input name="min_level" inputMode="decimal" placeholder="Ex.: 5" />
                      <span className={styles.fieldHint}>
                        Abaixo disso o item aparece como &ldquo;Estoque baixo&rdquo;. Salve vazio para tirar o
                        alerta. O mínimo atual aparece na lista do estoque.
                      </span>
                    </label>
                  </div>
                  <div className={styles.formActions}>
                    <button type="submit" className={styles.buttonGhost}>
                      Salvar mínimo
                    </button>
                  </div>
                </form>
              </Section>
            ) : null}
          </div>
        ) : null}
      </div>
    </>
  );
}
