import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { requireMe } from "@/lib/panel/api";
import { formatMoney, moneyInput } from "@/lib/panel/format";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type {
  CheckoutSettings,
  DeliveryWindow,
  DeliveryZone,
  FulfillmentSettings,
  PickupLocation,
} from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { PageHeader, Pill, Section, TableWrap } from "../ui";
import { saveCheckout, saveFulfillment } from "./actions";
import local from "./entrega.module.css";

export const metadata: Metadata = { title: "Entrega e checkout" };

const WEEKDAYS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"];

/** Campos de um local de retirada (linha `i` do formulário; `loc` vazio = local novo). */
function LocationFields({ loc, i }: { loc: PickupLocation | null; i: number }) {
  return (
    <>
      <input type="hidden" name={`loc_id_${i}`} value={loc?.id ?? ""} />
      <div className={styles.fields}>
        <label className={styles.field}>
          Nome do local
          <input name={`loc_name_${i}`} maxLength={80} defaultValue={loc?.name ?? ""} placeholder="Ex.: Loja do centro" />
          <span className={styles.fieldHint}>
            {loc ? "Apague o nome e salve para remover este local." : "Deixe vazio se não for usar."}
          </span>
        </label>
        <div className={local.checkCell}>
          <label className={styles.check}>
            <input type="checkbox" name={`loc_active_${i}`} defaultChecked={loc?.active ?? true} /> Ativo
          </label>
        </div>
        <label className={`${styles.field} ${styles.fieldWide}`}>
          Endereço
          <input name={`loc_address_${i}`} maxLength={300} defaultValue={loc?.address ?? ""} />
        </label>
        <label className={`${styles.field} ${styles.fieldWide}`}>
          Instruções para retirar
          <input
            name={`loc_instructions_${i}`}
            maxLength={300}
            defaultValue={loc?.instructions ?? ""}
            placeholder="Ex.: toque o interfone 12"
          />
        </label>
      </div>
    </>
  );
}

/** Campos de uma zona de entrega (linha `i`; `zone` vazio = zona nova). */
function ZoneFields({ zone, i }: { zone: DeliveryZone | null; i: number }) {
  return (
    <>
      <input type="hidden" name={`zone_id_${i}`} value={zone?.id ?? ""} />
      <div className={styles.fields}>
        <label className={styles.field}>
          Nome da zona
          <input name={`zone_name_${i}`} maxLength={80} defaultValue={zone?.name ?? ""} placeholder="Ex.: Centro" />
          <span className={styles.fieldHint}>
            {zone ? "Apague o nome e salve para remover esta zona." : "Deixe vazio se não for usar."}
          </span>
        </label>
        <label className={styles.field}>
          Tipo
          <select name={`zone_kind_${i}`} defaultValue={zone?.kind ?? "cep_ranges"}>
            <option value="cep_ranges">Faixas de CEP</option>
            <option value="districts">Bairros</option>
          </select>
        </label>
        <label className={styles.field}>
          Taxa de entrega (R$)
          <input name={`zone_fee_${i}`} inputMode="decimal" defaultValue={moneyInput(zone?.fee_cents ?? null)} placeholder="0,00" />
          <span className={styles.fieldHint}>Vazio = entrega grátis.</span>
        </label>
        <label className={styles.field}>
          Pedido mínimo (R$)
          <input
            name={`zone_min_${i}`}
            inputMode="decimal"
            defaultValue={moneyInput(zone?.min_order_cents ?? null)}
            placeholder="Opcional"
          />
        </label>
        <label className={styles.field}>
          Prazo (minutos)
          <input name={`zone_eta_${i}`} type="number" min={0} defaultValue={zone?.eta_minutes ?? ""} placeholder="Opcional" />
        </label>
        <div className={local.checkCell}>
          <label className={styles.check}>
            <input type="checkbox" name={`zone_active_${i}`} defaultChecked={zone?.active ?? true} /> Ativa
          </label>
        </div>
      </div>

      <div className={`${local.group} ${local.onlyCep}`}>
        <p className={local.groupLabel}>Por faixa de CEP</p>
        <div className={styles.fields}>
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Faixas de CEP
            <textarea
              name={`zone_ceps_${i}`}
              rows={2}
              defaultValue={(zone?.cep_ranges ?? []).map((r) => `${r.start} a ${r.end}`).join("\n")}
            />
            <span className={styles.fieldHint}>
              Uma por linha, como <code>01000-000 a 01099-999</code>.
            </span>
          </label>
        </div>
      </div>

      <div className={`${local.group} ${local.onlyDistricts}`}>
        <p className={local.groupLabel}>Por bairro</p>
        <div className={styles.fields}>
          <label className={styles.field}>
            Cidade
            <input name={`zone_city_${i}`} maxLength={80} defaultValue={zone?.city ?? ""} />
          </label>
          <label className={styles.field}>
            UF
            <input name={`zone_state_${i}`} maxLength={2} defaultValue={zone?.state ?? ""} placeholder="SP" />
          </label>
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Bairros
            <textarea name={`zone_districts_${i}`} rows={2} defaultValue={(zone?.districts ?? []).join("\n")} />
            <span className={styles.fieldHint}>Um por linha.</span>
          </label>
        </div>
      </div>
    </>
  );
}

function zoneSummary(zone: DeliveryZone): string {
  const parts = [
    zone.kind === "districts" ? "Bairros" : "Faixas de CEP",
    zone.fee_cents ? `taxa ${formatMoney(zone.fee_cents)}` : "entrega grátis",
  ];
  if (zone.min_order_cents) parts.push(`mínimo ${formatMoney(zone.min_order_cents)}`);
  if (zone.eta_minutes) parts.push(`${zone.eta_minutes} min`);
  return parts.join(" · ");
}

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
  const locations: (PickupLocation | null)[] = [...(cfg.pickup?.locations ?? []), ...Array(2).fill(null)].slice(0, 10);
  const zones: (DeliveryZone | null)[] = [...(cfg.delivery?.zones ?? []), null].slice(0, 50);
  const windows: (DeliveryWindow | null)[] = [...(cfg.scheduling?.windows ?? []), ...Array(2).fill(null)].slice(0, 28);
  const tenantField = <input type="hidden" name="tenant_id" value={context.tenant_id} />;

  // Linhas já salvas abrem sob demanda; as vazias (para criar) ficam num cartão "Adicionar".
  // O índice `i` é o da lista inteira: é ele que vai no nome dos campos.
  const locationRows = locations.map((loc, i) => ({ loc, i }));
  const savedLocations = locationRows.filter((row) => row.loc !== null);
  const newLocations = locationRows.filter((row) => row.loc === null);
  const zoneRows = zones.map((zone, i) => ({ zone, i }));
  const savedZones = zoneRows.filter((row) => row.zone !== null);
  const newZones = zoneRows.filter((row) => row.zone === null);

  return (
    <>
      <PageHeader
        eyebrow="Entrega e checkout"
        title="Como o pedido chega ao cliente"
        lead="Defina onde o cliente retira, até onde você entrega, em que horários e as regras para pagar e cancelar."
      />
      <Flash ok={ok} erro={erro} />
      {!features.pickup && !features.delivery ? (
        <p className={styles.note}>Retirada e entrega estão desligadas para a loja. Fale com o suporte para ligar.</p>
      ) : null}

      <form action={saveFulfillment}>
        {tenantField}
        <input type="hidden" name="loc_rows" value={locations.length} />
        <input type="hidden" name="zone_rows" value={zones.length} />
        <input type="hidden" name="win_rows" value={windows.length} />

        <Section
          title="Retirada na loja"
          description="O cliente busca o pedido num endereço seu"
          actions={features.pickup ? undefined : <Pill state="off">Módulo desligado</Pill>}
        >
          <div className={local.toggle}>
            <label className={styles.check}>
              <input type="checkbox" name="pickup_enabled" defaultChecked={cfg.pickup?.enabled ?? true} />
              <span className={local.checkText}>
                Oferecer retirada
                <span className={styles.fieldHint}>
                  {features.pickup
                    ? "Aparece como opção no checkout, com os locais ativos abaixo."
                    : "O módulo de retirada está desligado para a loja: fale com o suporte para ligar."}
                </span>
              </span>
            </label>
          </div>
          <div className={local.list}>
            {savedLocations.map(({ loc, i }) =>
              loc ? (
                <details key={loc.id} className={local.item}>
                  <summary>
                    <span className={local.itemTitle}>{loc.name}</span>
                    <span className={local.itemSub}>{loc.address}</span>
                    <Pill state={loc.active ? "live" : "off"}>{loc.active ? "Ativo" : "Desligado"}</Pill>
                  </summary>
                  <div className={local.itemBody}>
                    <LocationFields loc={loc} i={i} />
                  </div>
                </details>
              ) : null,
            )}
            {newLocations.length ? (
              <details className={`${local.item} ${local.add}`} open={features.pickup && savedLocations.length === 0}>
                <summary>{savedLocations.length ? "Adicionar outro local" : "Cadastrar um local de retirada"}</summary>
                <div className={local.itemBody}>
                  {newLocations.map(({ i }) => (
                    <div key={`novo-${i}`} className={local.newRow}>
                      <p className={local.groupLabel}>Novo local</p>
                      <LocationFields loc={null} i={i} />
                    </div>
                  ))}
                </div>
              </details>
            ) : null}
          </div>
        </Section>

        <Section
          title="Entrega própria"
          description="Até onde você leva e quanto cobra"
          actions={features.delivery ? undefined : <Pill state="off">Módulo desligado</Pill>}
        >
          <div className={local.toggle}>
            <label className={styles.check}>
              <input type="checkbox" name="delivery_enabled" defaultChecked={cfg.delivery?.enabled ?? false} />
              <span className={local.checkText}>
                Oferecer entrega
                <span className={styles.fieldHint}>
                  {features.delivery
                    ? "O cliente informa o endereço e vê a taxa da zona que atende."
                    : "O módulo de entrega está desligado para a loja: fale com o suporte para ligar."}
                </span>
              </span>
            </label>
          </div>
          <p className={local.intro}>
            Monte zonas por faixa de CEP (uma por linha, <code>01000-000 a 01099-999</code>) ou por bairro (um por
            linha, com cidade e UF). Vale a primeira zona ativa que atende o endereço; faixas de CEP são conferidas
            antes dos bairros.
          </p>
          <div className={local.list}>
            {savedZones.map(({ zone, i }) =>
              zone ? (
                <details key={zone.id} className={`${local.item} ${local.zone}`}>
                  <summary>
                    <span className={local.itemTitle}>{zone.name}</span>
                    <span className={local.itemSub}>{zoneSummary(zone)}</span>
                    <Pill state={zone.active ? "live" : "off"}>{zone.active ? "Ativa" : "Desligada"}</Pill>
                  </summary>
                  <div className={local.itemBody}>
                    <ZoneFields zone={zone} i={i} />
                  </div>
                </details>
              ) : null,
            )}
            {newZones.map(({ i }) => (
              <details
                key={`nova-${i}`}
                className={`${local.item} ${local.add} ${local.zone}`}
                open={features.delivery && savedZones.length === 0}
              >
                <summary>{savedZones.length ? "Adicionar outra zona" : "Criar a primeira zona de entrega"}</summary>
                <div className={local.itemBody}>
                  <ZoneFields zone={null} i={i} />
                </div>
              </details>
            ))}
          </div>
          {savedZones.length > 0 && newZones.length > 0 ? (
            <p className={styles.hint}>Para criar mais de uma zona, salve e adicione a próxima.</p>
          ) : null}
        </Section>

        <Section title="Pedido mínimo e horários" description="Vale para retirada e entrega">
          <div className={styles.fields}>
            <label className={styles.field}>
              Pedido mínimo da loja (R$)
              <input
                name="min_order"
                inputMode="decimal"
                defaultValue={moneyInput(cfg.min_order_cents ?? null)}
                placeholder="Sem mínimo"
              />
              <span className={styles.fieldHint}>Vazio = qualquer valor. Cada zona pode pedir um mínimo próprio.</span>
            </label>
          </div>

          <h4 className={local.subhead}>Agendamento</h4>
          <div className={styles.fields}>
            <label className={`${styles.check} ${styles.fieldWide}`}>
              <input type="checkbox" name="scheduling_enabled" defaultChecked={cfg.scheduling?.enabled ?? false} />
              <span className={local.checkText}>
                Cliente escolhe o horário
                <span className={styles.fieldHint}>Ele escolhe um dos horários da tabela abaixo ao fechar o pedido.</span>
              </span>
            </label>
            <label className={styles.field}>
              Antecedência mínima (minutos)
              <input
                name="min_lead_minutes"
                type="number"
                min={0}
                defaultValue={cfg.scheduling?.min_lead_minutes ?? 60}
              />
              <span className={styles.fieldHint}>Tempo mínimo entre o pedido e o horário escolhido.</span>
            </label>
            <label className={styles.field}>
              Dias à frente
              <input name="days_ahead" type="number" min={1} max={30} defaultValue={cfg.scheduling?.days_ahead ?? 7} />
              <span className={styles.fieldHint}>Até quantos dias o cliente pode agendar (1 a 30).</span>
            </label>
          </div>

          <h4 className={local.subhead}>Horários de atendimento</h4>
          <TableWrap>
            <table className={`${styles.table} ${local.windows}`}>
              <thead>
                <tr>
                  <th>Dia</th>
                  <th>Das</th>
                  <th>Às</th>
                  <th className={local.center}>Retirada</th>
                  <th className={local.center}>Entrega</th>
                </tr>
              </thead>
              <tbody>
                {windows.map((window, i) => (
                  <tr key={i}>
                    <td>
                      <select name={`win_weekday_${i}`} defaultValue={window?.weekday ?? 0} aria-label={`Dia do horário ${i + 1}`}>
                        {WEEKDAYS.map((label, day) => (
                          <option key={label} value={day}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        name={`win_start_${i}`}
                        type="time"
                        defaultValue={window?.start ?? ""}
                        aria-label={`Início do horário ${i + 1}`}
                      />
                    </td>
                    <td>
                      <input
                        name={`win_end_${i}`}
                        type="time"
                        defaultValue={window?.end ?? ""}
                        aria-label={`Fim do horário ${i + 1}`}
                      />
                    </td>
                    <td className={local.center}>
                      <input
                        type="checkbox"
                        name={`win_pickup_${i}`}
                        defaultChecked={window ? window.modes.includes("pickup") : true}
                        aria-label={`Retirada no horário ${i + 1}`}
                      />
                    </td>
                    <td className={local.center}>
                      <input
                        type="checkbox"
                        name={`win_delivery_${i}`}
                        defaultChecked={window ? window.modes.includes("delivery") : true}
                        aria-label={`Entrega no horário ${i + 1}`}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <p className={styles.hint}>
            Uma linha por faixa de horário. Linha sem início ou fim é ignorada: apague os horários para tirar uma
            linha. Sempre aparecem duas linhas vazias; para mais, salve e continue.
          </p>
        </Section>

        <div className={local.saveBar}>
          <p>Retirada, entrega e horários são salvos juntos.</p>
          <button type="submit" className={styles.button}>
            Salvar entrega
          </button>
        </div>
      </form>

      {features.checkout ? (
        <Section title="Checkout" description="Prazo para pagar, cancelamento e devoluções">
          <form action={saveCheckout}>
            {tenantField}
            <div className={styles.fields}>
              <label className={styles.field}>
                Prazo para pagar (minutos)
                <input
                  name="pix_ttl_minutes"
                  type="number"
                  min={5}
                  max={1440}
                  defaultValue={checkout.pix_ttl_minutes ?? 30}
                />
                <span className={styles.fieldHint}>Depois disso, o pedido sem pagamento expira (5 a 1440).</span>
              </label>
              <label className={styles.field}>
                Pedidos em aberto por cliente
                <input
                  name="max_open_orders"
                  type="number"
                  min={1}
                  max={10}
                  defaultValue={checkout.max_open_orders ?? 3}
                />
                <span className={styles.fieldHint}>Quantos pedidos esperando pagamento cada cliente pode ter (1 a 10).</span>
              </label>
              <label className={styles.field}>
                Cliente cancela pedido pago até
                <select name="customer_cancel_until" defaultValue={checkout.customer_cancel_until ?? "accepted"}>
                  <option value="payment_confirmed">pagamento confirmado</option>
                  <option value="accepted">pedido aceito</option>
                </select>
                <span className={styles.fieldHint}>Depois disso, só a loja cancela.</span>
              </label>
              <label className={styles.field}>
                Reembolso acima de (R$) pede segunda aprovação
                <input
                  name="refund_threshold"
                  inputMode="decimal"
                  defaultValue={moneyInput(checkout.refund_four_eyes_threshold_cents ?? 20000)}
                />
                <span className={styles.fieldHint}>Acima desse valor, outra pessoa da loja precisa aprovar a devolução.</span>
              </label>
              <label className={`${styles.check} ${styles.fieldWide}`}>
                <input type="checkbox" name="auto_accept" defaultChecked={checkout.auto_accept ?? false} />
                <span className={local.checkText}>
                  Aceitar pedidos pagos automaticamente
                  <span className={styles.fieldHint}>Sem isso, você aceita cada pedido pago na aba Pedidos.</span>
                </span>
              </label>
            </div>
            <div className={styles.formActions}>
              <button type="submit" className={styles.button}>
                Salvar checkout
              </button>
            </div>
          </form>
        </Section>
      ) : null}
    </>
  );
}
