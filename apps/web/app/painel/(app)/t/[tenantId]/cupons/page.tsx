import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { moneyInput } from "@/lib/panel/format";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Coupon, Page } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { createCoupon, updateCoupon } from "./actions";

export const metadata: Metadata = { title: "Cupons" };

function dayInStore(iso: string | null, timezone: string): string {
  if (!iso) return "";
  const parts = new Intl.DateTimeFormat("en-CA", { timeZone: timezone }).format(new Date(iso));
  return parts; // YYYY-MM-DD, what <input type="date"> wants
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

  return (
    <>
      <h2>Cupons</h2>
      <Flash ok={ok} erro={erro} />
      <p className="muted">
        O desconto vale sobre os produtos, nunca sobre a entrega. Um cupom com limite é usado só
        aquele número de vezes; se um pedido expira sem pagamento, a vaga volta para o cupom.
      </p>

      <section className={styles.card}>
        <h2>Novo cupom</h2>
        <form action={createCoupon} className={styles.form}>
          {shared}
          <label>
            Código
            <input name="code" maxLength={40} required placeholder="BEMVINDO" />
          </label>
          <label>
            Tipo
            <select name="kind" defaultValue="percent">
              <option value="percent">Porcentagem</option>
              <option value="fixed">Valor fixo</option>
            </select>
          </label>
          <label>
            Porcentagem (%)
            <input name="percent" inputMode="decimal" placeholder="10" />
          </label>
          <label>
            Valor fixo
            <input name="amount" inputMode="decimal" placeholder="15,00" />
          </label>
          <label>
            Desconto máximo (porcentagem)
            <input name="max_discount" inputMode="decimal" placeholder="20,00" />
          </label>
          <label>
            Pedido mínimo
            <input name="min_subtotal" inputMode="decimal" placeholder="50,00" />
          </label>
          <label>
            Começa em
            <input type="date" name="starts_at" />
          </label>
          <label>
            Termina em
            <input type="date" name="ends_at" />
          </label>
          <label>
            Usos no total
            <input type="number" name="max_redemptions" min={1} placeholder="sem limite" />
          </label>
          <label>
            Usos por cliente
            <input type="number" name="per_customer_limit" min={1} placeholder="sem limite" />
          </label>
          <label style={{ flexGrow: 2 }}>
            Observação
            <input name="note" maxLength={200} placeholder="campanha de setembro" />
          </label>
          <button type="submit">Criar cupom</button>
        </form>
      </section>

      {page.items.length === 0 ? <p className="muted">Nenhum cupom criado ainda.</p> : null}
      {page.items.map((coupon) => (
        <section key={coupon.id} className={styles.card}>
          <h2>
            {coupon.code} —{" "}
            {coupon.kind === "percent"
              ? `${(coupon.percent_bps ?? 0) / 100}%`
              : moneyInput(coupon.amount_cents ?? 0)}
            {coupon.status === "active" ? "" : ` (${coupon.status === "paused" ? "pausado" : "arquivado"})`}
          </h2>
          <p className="muted">
            Usado {coupon.redemptions_count}
            {coupon.max_redemptions ? ` de ${coupon.max_redemptions}` : ""} vez(es)
            {coupon.per_customer_limit ? ` · até ${coupon.per_customer_limit} por cliente` : ""}
            {coupon.min_subtotal_cents ? ` · pedido mínimo ${moneyInput(coupon.min_subtotal_cents)}` : ""}
            {coupon.note ? ` · ${coupon.note}` : ""}
          </p>
          <form action={updateCoupon} className={styles.form}>
            {shared}
            <input type="hidden" name="coupon_id" value={coupon.id} />
            <input type="hidden" name="kind" value={coupon.kind} />
            {coupon.kind === "percent" ? (
              <>
                <label>
                  Porcentagem (%)
                  <input name="percent" inputMode="decimal" defaultValue={(coupon.percent_bps ?? 0) / 100} />
                </label>
                <label>
                  Desconto máximo
                  <input
                    name="max_discount"
                    inputMode="decimal"
                    defaultValue={coupon.max_discount_cents ? moneyInput(coupon.max_discount_cents) : ""}
                  />
                </label>
              </>
            ) : (
              <label>
                Valor fixo
                <input name="amount" inputMode="decimal" defaultValue={moneyInput(coupon.amount_cents ?? 0)} />
              </label>
            )}
            <label>
              Pedido mínimo
              <input
                name="min_subtotal"
                inputMode="decimal"
                defaultValue={coupon.min_subtotal_cents ? moneyInput(coupon.min_subtotal_cents) : ""}
              />
            </label>
            <label>
              Começa em
              <input type="date" name="starts_at" defaultValue={dayInStore(coupon.starts_at, timezone)} />
            </label>
            <label>
              Termina em
              <input type="date" name="ends_at" defaultValue={dayInStore(coupon.ends_at, timezone)} />
            </label>
            <label>
              Usos no total
              <input type="number" name="max_redemptions" min={1} defaultValue={coupon.max_redemptions ?? ""} />
            </label>
            <label>
              Usos por cliente
              <input
                type="number"
                name="per_customer_limit"
                min={1}
                defaultValue={coupon.per_customer_limit ?? ""}
              />
            </label>
            <label>
              Situação
              <select name="status" defaultValue={coupon.status}>
                <option value="active">Valendo</option>
                <option value="paused">Pausado</option>
                <option value="archived">Arquivado</option>
              </select>
            </label>
            <label style={{ flexGrow: 2 }}>
              Observação
              <input name="note" maxLength={200} defaultValue={coupon.note ?? ""} />
            </label>
            <button type="submit">Salvar</button>
          </form>
        </section>
      ))}
    </>
  );
}
