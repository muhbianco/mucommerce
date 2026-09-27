import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { api, requireMe } from "@/lib/panel/api";
import { formatMoney, moneyInput } from "@/lib/panel/format";
import { tenantScopes } from "@/lib/panel/scopes";
import type { PillState } from "@/lib/panel/states";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Coupon, Page } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { EmptyState, PageHeader, Pill, Section } from "../ui";
import { createCoupon, updateCoupon } from "./actions";
import local from "./cupons.module.css";

export const metadata: Metadata = { title: "Cupons" };

function dayInStore(iso: string | null, timezone: string): string {
  if (!iso) return "";
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: timezone }).format(new Date(iso));
  return parts; // YYYY-MM-DD, what <input type="date"> wants
}

/** Data para ler (27/09/2026), no fuso da loja. */
function dayLabel(iso: string, timezone: string): string {
  return new Intl.DateTimeFormat("pt-BR", { timeZone: timezone, dateStyle: "short" }).format(new Date(iso));
}

const STATUS_LABEL: Record<string, string> = { paused: "Pausado", archived: "Arquivado" };

/** Situação do cupom na hora, na mesma ordem em que o checkout recusa. */
function couponState(coupon: Coupon, now: number, timezone: string): { state: PillState; label: string } {
  if (coupon.status !== "active") return { state: "off", label: STATUS_LABEL[coupon.status] ?? coupon.status };
  if (coupon.starts_at && now < Date.parse(coupon.starts_at)) {
    return { state: "pending", label: `Começa em ${dayLabel(coupon.starts_at, timezone)}` };
  }
  if (coupon.ends_at && now >= Date.parse(coupon.ends_at)) return { state: "off", label: "Encerrado" };
  if (coupon.max_redemptions !== null && coupon.redemptions_count >= coupon.max_redemptions) {
    return { state: "warn", label: "Esgotado" };
  }
  return { state: "live", label: "Valendo" };
}

/** "10% de desconto (até R$ 20,00) · pedido mínimo R$ 50,00 · termina em 31/10/2026" */
function couponTerms(coupon: Coupon, timezone: string): string {
  const parts = [
    coupon.kind === "percent"
      ? `${((coupon.percent_bps ?? 0) / 100).toLocaleString("pt-BR")}% de desconto${
          coupon.max_discount_cents ? ` (até ${formatMoney(coupon.max_discount_cents)})` : ""
        }`
      : `${formatMoney(coupon.amount_cents ?? 0)} de desconto`,
  ];
  if (coupon.min_subtotal_cents) parts.push(`pedido mínimo ${formatMoney(coupon.min_subtotal_cents)}`);
  if (coupon.starts_at) parts.push(`começa em ${dayLabel(coupon.starts_at, timezone)}`);
  if (coupon.ends_at) parts.push(`termina em ${dayLabel(coupon.ends_at, timezone)}`);
  return parts.join(" · ");
}

/** "3 de 10 usos · até 1 por cliente" */
function couponUsage(coupon: Coupon): string {
  const used = coupon.redemptions_count;
  const parts = [
    coupon.max_redemptions ? `${used} de ${coupon.max_redemptions} usos` : `${used} ${used === 1 ? "uso" : "usos"}`,
  ];
  if (coupon.per_customer_limit) parts.push(`até ${coupon.per_customer_limit} por cliente`);
  return parts.join(" · ");
}

export default async function Coupons({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!context.features.coupons || !tenantScopes(me, context.tenant_id).can("settings:write")) {
    notFound();
  }
  const page = await api<Page<Coupon>>(`/admin/tenants/${tenantId}/coupons?limit=50`);
  const timezone = context.timezone;
  const shared = (
    <>
      <input type="hidden" name="tenant_id" value={context.tenant_id} />
      <input type="hidden" name="timezone" value={timezone} />
    </>
  );
  const now = Date.now();
  const current = page.items.filter((coupon) => coupon.status !== "archived");
  const archived = page.items.filter((coupon) => coupon.status === "archived");

  /** Linha do cupom: resumo, situação e o formulário de edição, que abre sob demanda. */
  const row = (coupon: Coupon): ReactNode => {
    const view = couponState(coupon, now, timezone);
    return (
      <div key={coupon.id} className={styles.row}>
        <div className={styles.rowMain}>
          <div className={local.code}>{coupon.code}</div>
          <p className={styles.rowSub}>{couponTerms(coupon, timezone)}</p>
          <p className={styles.rowSub}>{couponUsage(coupon)}</p>
          {coupon.note ? <p className={`${styles.rowSub} ${local.note}`}>{coupon.note}</p> : null}
        </div>
        <div className={styles.rowBadges}>
          <Pill state={view.state}>{view.label}</Pill>
        </div>
        <details className={`${styles.rowDetail} ${local.edit}`}>
          <summary>Editar cupom</summary>
          <form action={updateCoupon} className={local.editBody}>
            {shared}
            <input type="hidden" name="coupon_id" value={coupon.id} />
            <input type="hidden" name="kind" value={coupon.kind} />
            <div className={styles.fields}>
              {coupon.kind === "percent" ? (
                <>
                  <label className={styles.field}>
                    Porcentagem (%)
                    <input name="percent" inputMode="decimal" defaultValue={(coupon.percent_bps ?? 0) / 100} />
                  </label>
                  <label className={styles.field}>
                    Desconto máximo (R$)
                    <input
                      name="max_discount"
                      inputMode="decimal"
                      defaultValue={coupon.max_discount_cents ? moneyInput(coupon.max_discount_cents) : ""}
                      placeholder="Sem teto"
                    />
                  </label>
                </>
              ) : (
                <label className={styles.field}>
                  Valor fixo (R$)
                  <input name="amount" inputMode="decimal" defaultValue={moneyInput(coupon.amount_cents ?? 0)} />
                </label>
              )}
              <label className={styles.field}>
                Pedido mínimo (R$)
                <input
                  name="min_subtotal"
                  inputMode="decimal"
                  defaultValue={coupon.min_subtotal_cents ? moneyInput(coupon.min_subtotal_cents) : ""}
                  placeholder="Sem mínimo"
                />
              </label>
              <label className={styles.field}>
                Começa em
                <input type="date" name="starts_at" defaultValue={dayInStore(coupon.starts_at, timezone)} />
              </label>
              <label className={styles.field}>
                Termina em
                <input type="date" name="ends_at" defaultValue={dayInStore(coupon.ends_at, timezone)} />
              </label>
              <label className={styles.field}>
                Usos no total
                <input
                  type="number"
                  name="max_redemptions"
                  min={1}
                  defaultValue={coupon.max_redemptions ?? ""}
                  placeholder="Sem limite"
                />
              </label>
              <label className={styles.field}>
                Usos por cliente
                <input
                  type="number"
                  name="per_customer_limit"
                  min={1}
                  defaultValue={coupon.per_customer_limit ?? ""}
                  placeholder="Sem limite"
                />
              </label>
              <label className={styles.field}>
                Situação
                <select name="status" defaultValue={coupon.status}>
                  <option value="active">Valendo</option>
                  <option value="paused">Pausado</option>
                  <option value="archived">Arquivado</option>
                </select>
                <span className={styles.fieldHint}>Pausado e arquivado não valem no checkout.</span>
              </label>
              <label className={`${styles.field} ${styles.fieldWide}`}>
                Observação
                <input name="note" maxLength={200} defaultValue={coupon.note ?? ""} />
              </label>
            </div>
            <div className={styles.formActions}>
              <button type="submit" className={styles.button}>
                Salvar cupom
              </button>
            </div>
          </form>
        </details>
      </div>
    );
  };

  return (
    <>
      <PageHeader
        eyebrow="Cupons"
        title="Cupons de desconto"
        lead="Crie códigos de desconto para os seus clientes digitarem na hora de pagar."
        actions={
          current.length ? (
            <a href="#novo-cupom" className={styles.button}>
              Novo cupom
            </a>
          ) : undefined
        }
      />
      <Flash ok={ok} erro={erro} />

      <Section
        title="Seus cupons"
        description={current.length === 1 ? "1 cupom" : `${current.length} cupons`}
      >
        {current.length ? (
          <div className={styles.rows}>{current.map(row)}</div>
        ) : archived.length ? (
          <EmptyState title="Nenhum cupom valendo ou pausado">
            Os cupons arquivados estão no fim da página; dá para reativar qualquer um deles.
          </EmptyState>
        ) : (
          <EmptyState
            title="Nenhum cupom criado ainda"
            action={
              <a href="#novo-cupom" className={styles.buttonGhost}>
                Criar o primeiro cupom
              </a>
            }
          >
            Crie um código, como BEMVINDO, e divulgue para os seus clientes.
          </EmptyState>
        )}
        {page.next_cursor ? <p className={styles.hint}>Mostrando os 50 primeiros cupons.</p> : null}
      </Section>

      <Section id="novo-cupom" title="Novo cupom" description="O cliente digita o código na hora de pagar">
        <ul className={local.rules}>
          <li>O desconto vale sobre os produtos, nunca sobre a entrega.</li>
          <li>
            Um cupom com limite é usado só aquele número de vezes; se um pedido expira sem pagamento, a vaga
            volta para o cupom.
          </li>
        </ul>
        <form action={createCoupon} className={local.newForm}>
          {shared}
          <div className={styles.fields}>
            <label className={styles.field}>
              Código
              <input name="code" maxLength={40} required placeholder="BEMVINDO" />
              <span className={styles.fieldHint}>De 3 a 40 letras, números, hífen ou _. Maiúscula ou minúscula tanto faz.</span>
            </label>
            <label className={styles.field}>
              Tipo
              <select name="kind" defaultValue="percent">
                <option value="percent">Porcentagem</option>
                <option value="fixed">Valor fixo</option>
              </select>
            </label>
            <label className={`${styles.field} ${local.onlyPercent}`}>
              Porcentagem (%)
              <input name="percent" inputMode="decimal" placeholder="10" />
            </label>
            <label className={`${styles.field} ${local.onlyPercent}`}>
              Desconto máximo (R$)
              <input name="max_discount" inputMode="decimal" placeholder="20,00" />
              <span className={styles.fieldHint}>Opcional. Teto do desconto em reais, para cupom de porcentagem.</span>
            </label>
            <label className={`${styles.field} ${local.onlyFixed}`}>
              Valor fixo (R$)
              <input name="amount" inputMode="decimal" placeholder="15,00" />
              <span className={styles.fieldHint}>Só para cupom de valor fixo.</span>
            </label>
            <label className={styles.field}>
              Pedido mínimo (R$)
              <input name="min_subtotal" inputMode="decimal" placeholder="50,00" />
              <span className={styles.fieldHint}>Opcional.</span>
            </label>
            <label className={styles.field}>
              Começa em
              <input type="date" name="starts_at" />
              <span className={styles.fieldHint}>Vazio = vale a partir de agora.</span>
            </label>
            <label className={styles.field}>
              Termina em
              <input type="date" name="ends_at" />
              <span className={styles.fieldHint}>Vazio = sem data para acabar.</span>
            </label>
            <label className={styles.field}>
              Usos no total
              <input type="number" name="max_redemptions" min={1} placeholder="Sem limite" />
            </label>
            <label className={styles.field}>
              Usos por cliente
              <input type="number" name="per_customer_limit" min={1} placeholder="Sem limite" />
            </label>
            <label className={`${styles.field} ${styles.fieldWide}`}>
              Observação
              <input name="note" maxLength={200} placeholder="Ex.: campanha de setembro" />
              <span className={styles.fieldHint}>Só aparece aqui no painel.</span>
            </label>
          </div>
          <div className={styles.formActions}>
            <button type="submit" className={styles.button}>
              Criar cupom
            </button>
          </div>
        </form>
      </Section>

      {archived.length ? (
        <details className={styles.card}>
          <summary>Cupons arquivados ({archived.length})</summary>
          <div className={`${styles.rows} ${local.archivedRows}`}>{archived.map(row)}</div>
        </details>
      ) : null}
    </>
  );
}
