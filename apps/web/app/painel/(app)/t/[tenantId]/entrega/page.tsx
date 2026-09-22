import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { requireMe } from "@/lib/panel/api";
import { moneyInput } from "@/lib/panel/format";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { CheckoutSettings, FulfillmentSettings } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { saveCheckout, saveFulfillment } from "./actions";

export const metadata: Metadata = { title: "Entrega e checkout" };

const WEEKDAYS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"];

export default async function Fulfillment({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!tenantScopes(me, context.tenant_id).can("settings:write")) notFound();
  const { features } = context;
  if (!(features.checkout || features.pickup || features.delivery)) notFound();

  const cfg = (context.settings.fulfillment ?? {}) as Partial<FulfillmentSettings>;
  const checkout = (context.settings.checkout ?? {}) as Partial<CheckoutSettings>;
  const locations = [...(cfg.pickup?.locations ?? []), ...Array(2).fill(null)].slice(0, 10);
  const zones = [...(cfg.delivery?.zones ?? []), null].slice(0, 50);
  const windows = [...(cfg.scheduling?.windows ?? []), ...Array(2).fill(null)].slice(0, 28);
  const tenantField = <input type="hidden" name="tenant_id" value={context.tenant_id} />;

  return (
    <>
      <h2>Entrega e checkout</h2>
      <Flash ok={ok} erro={erro} />
      {!features.pickup && !features.delivery ? (
        <p className="muted">Retirada e entrega estão desligadas para a loja. Fale com o suporte para ligar.</p>
      ) : null}

      <form action={saveFulfillment}>
        {tenantField}
        <input type="hidden" name="loc_rows" value={locations.length} />
        <input type="hidden" name="zone_rows" value={zones.length} />
        <input type="hidden" name="win_rows" value={windows.length} />

        <section className={styles.card}>
          <h2>Retirada</h2>
          <label>
            <input type="checkbox" name="pickup_enabled" defaultChecked={cfg.pickup?.enabled ?? true} /> Oferecer
            retirada {features.pickup ? "" : "(módulo desligado)"}
          </label>
          {locations.map((loc, i) => (
            <div key={loc?.id ?? `novo-${i}`} className={styles.form}>
              <input type="hidden" name={`loc_id_${i}`} value={loc?.id ?? ""} />
              <label>
                Local
                <input name={`loc_name_${i}`} maxLength={80} defaultValue={loc?.name ?? ""} placeholder="ex.: Loja do centro" />
              </label>
              <label style={{ flexGrow: 2 }}>
                Endereço
                <input name={`loc_address_${i}`} maxLength={300} defaultValue={loc?.address ?? ""} />
              </label>
              <label style={{ flexGrow: 2 }}>
                Instruções
                <input name={`loc_instructions_${i}`} maxLength={300} defaultValue={loc?.instructions ?? ""} />
              </label>
              <label>
                <input type="checkbox" name={`loc_active_${i}`} defaultChecked={loc?.active ?? true} /> ativo
              </label>
            </div>
          ))}
        </section>

        <section className={styles.card}>
          <h2>Entrega própria</h2>
          <label>
            <input type="checkbox" name="delivery_enabled" defaultChecked={cfg.delivery?.enabled ?? false} /> Oferecer
            entrega {features.delivery ? "" : "(módulo desligado)"}
          </label>
          <p className="muted">
            Por faixa de CEP (uma por linha, <code>01000-000 a 01099-999</code>) ou por bairro (um por linha, com cidade e
            UF). Vale a primeira zona ativa que atende o endereço; faixas de CEP são conferidas antes dos bairros.
          </p>
          {zones.map((zone, i) => (
            <fieldset key={zone?.id ?? `nova-${i}`} className={styles.form} style={{ flexBasis: "100%" }}>
              <legend>{zone ? zone.name : "Nova zona"}</legend>
              <input type="hidden" name={`zone_id_${i}`} value={zone?.id ?? ""} />
              <label>
                Nome
                <input name={`zone_name_${i}`} maxLength={80} defaultValue={zone?.name ?? ""} />
              </label>
              <label>
                Tipo
                <select name={`zone_kind_${i}`} defaultValue={zone?.kind ?? "cep_ranges"}>
                  <option value="cep_ranges">Faixas de CEP</option>
                  <option value="districts">Bairros</option>
                </select>
              </label>
              <label>
                Taxa (R$)
                <input name={`zone_fee_${i}`} inputMode="decimal" defaultValue={moneyInput(zone?.fee_cents ?? null)} style={{ width: "7rem" }} />
              </label>
              <label>
                Pedido mínimo
                <input name={`zone_min_${i}`} inputMode="decimal" defaultValue={moneyInput(zone?.min_order_cents ?? null)} style={{ width: "7rem" }} />
              </label>
              <label>
                Prazo (min)
                <input name={`zone_eta_${i}`} type="number" min={0} defaultValue={zone?.eta_minutes ?? ""} style={{ width: "6rem" }} />
              </label>
              <label>
                <input type="checkbox" name={`zone_active_${i}`} defaultChecked={zone?.active ?? true} /> ativa
              </label>
              <label style={{ flexBasis: "100%" }}>
                Faixas de CEP
                <textarea
                  name={`zone_ceps_${i}`}
                  rows={2}
                  defaultValue={(zone?.cep_ranges ?? []).map((r) => `${r.start} a ${r.end}`).join("\n")}
                />
              </label>
              <label>
                Cidade
                <input name={`zone_city_${i}`} maxLength={80} defaultValue={zone?.city ?? ""} />
              </label>
              <label>
                UF
                <input name={`zone_state_${i}`} maxLength={2} defaultValue={zone?.state ?? ""} style={{ width: "4rem" }} />
              </label>
              <label style={{ flexBasis: "100%" }}>
                Bairros
                <textarea name={`zone_districts_${i}`} rows={2} defaultValue={(zone?.districts ?? []).join("\n")} />
              </label>
            </fieldset>
          ))}
        </section>

        <section className={styles.card}>
          <h2>Pedido mínimo e horários</h2>
          <div className={styles.form}>
            <label>
              Pedido mínimo da loja (R$)
              <input name="min_order" inputMode="decimal" defaultValue={moneyInput(cfg.min_order_cents ?? null)} style={{ width: "8rem" }} />
            </label>
            <label>
              <input type="checkbox" name="scheduling_enabled" defaultChecked={cfg.scheduling?.enabled ?? false} /> Cliente
              escolhe o horário
            </label>
            <label>
              Antecedência mínima (min)
              <input name="min_lead_minutes" type="number" min={0} defaultValue={cfg.scheduling?.min_lead_minutes ?? 60} style={{ width: "6rem" }} />
            </label>
            <label>
              Dias à frente
              <input name="days_ahead" type="number" min={1} max={30} defaultValue={cfg.scheduling?.days_ahead ?? 7} style={{ width: "5rem" }} />
            </label>
          </div>
          {windows.map((window, i) => (
            <div key={i} className={styles.form}>
              <label>
                Dia
                <select name={`win_weekday_${i}`} defaultValue={window?.weekday ?? 0}>
                  {WEEKDAYS.map((label, day) => (
                    <option key={label} value={day}>
                      {label}
                    </option>
                  ))}
                </select>
              </label>
              <label>
                Das
                <input name={`win_start_${i}`} type="time" defaultValue={window?.start ?? ""} />
              </label>
              <label>
                às
                <input name={`win_end_${i}`} type="time" defaultValue={window?.end ?? ""} />
              </label>
              <label>
                <input type="checkbox" name={`win_pickup_${i}`} defaultChecked={window ? window.modes.includes("pickup") : true} /> retirada
              </label>
              <label>
                <input type="checkbox" name={`win_delivery_${i}`} defaultChecked={window ? window.modes.includes("delivery") : true} /> entrega
              </label>
            </div>
          ))}
          <button type="submit" className={styles.button}>
            Salvar entrega
          </button>
        </section>
      </form>

      {features.checkout ? (
        <section className={styles.card}>
          <h2>Checkout</h2>
          <form action={saveCheckout} className={styles.form}>
            {tenantField}
            <label>
              Prazo para pagar (min)
              <input name="pix_ttl_minutes" type="number" min={5} max={1440} defaultValue={checkout.pix_ttl_minutes ?? 30} style={{ width: "6rem" }} />
            </label>
            <label>
              Pedidos em aberto por cliente
              <input name="max_open_orders" type="number" min={1} max={10} defaultValue={checkout.max_open_orders ?? 3} style={{ width: "5rem" }} />
            </label>
            <label>
              Cliente cancela pedido pago até
              <select name="customer_cancel_until" defaultValue={checkout.customer_cancel_until ?? "accepted"}>
                <option value="payment_confirmed">pagamento confirmado</option>
                <option value="accepted">pedido aceito</option>
              </select>
            </label>
            <label>
              Reembolso acima de (R$) pede segunda aprovação
              <input name="refund_threshold" inputMode="decimal" defaultValue={moneyInput(checkout.refund_four_eyes_threshold_cents ?? 20000)} style={{ width: "8rem" }} />
            </label>
            <label>
              <input type="checkbox" name="auto_accept" defaultChecked={checkout.auto_accept ?? false} /> Aceitar pedidos pagos
              automaticamente
            </label>
            <button type="submit" className={styles.button}>
              Salvar checkout
            </button>
          </form>
        </section>
      ) : null}
    </>
  );
}
