import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { api, requireMe } from "@/lib/panel/api";
import { weightLabel } from "@/lib/panel/measure";
import { frozenPlanOf } from "@/lib/panel/packaging";
import { tenantScopes } from "@/lib/panel/scopes";
import {
  ORDER_STATE,
  PAYMENT_STATE,
  type PillState,
  REFUND_STATE,
  SHIPMENT_STATE,
  stateOf,
} from "@/lib/panel/states";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import {
  DELIVERY_STATUS_LABEL,
  ORDER_STATUS_LABEL,
  type OrderDetail,
  PAYMENT_METHOD_LABEL,
  PAYMENT_PROVIDERS,
  PAYMENT_STATUS_LABEL,
  REFUND_KIND_LABEL,
  REFUND_STATUS_LABEL,
  type Shipment,
  SHIPMENT_PREVIEW_PROBLEM,
  SHIPMENT_STATUS_LABEL,
  type ShipmentPreview,
  TRANSITION_LABEL,
} from "@/lib/panel/types";

import styles from "../../../../../panel.module.css";
import { Flash } from "../../flash";
import { KeyValues, PageHeader, Pill, Section, TableWrap } from "../../ui";
import { decideRefund, dispatchShipment, moveOrder, requestRefund } from "../actions";
import local from "./order.module.css";
import { PackingList } from "./packing-list";

export const metadata: Metadata = { title: "Pedido" };

const ID = /^[0-9a-f-]{36}$/;
const RISK_LABEL: Record<string, string> = {
  late_payment: "pagamento chegou fora do prazo",
  duplicate_payment: "pagamento em dobro",
  chargeback: "contestação no cartão",
  late_payment_refunded: "pagamento fora do prazo devolvido",
  duplicate_payment_refunded: "pagamento em dobro devolvido",
};

/** Cor do selo de cada e-mail enviado ao cliente. */
const EMAIL_STATE: Record<string, PillState> = {
  queued: "pending",
  sending: "pending",
  sent: "live",
  failed: "warn",
  skipped: "off",
};

function money(cents: number, currency: string): string {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency }).format(cents / 100);
}

export default async function OrderPage({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string; orderId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId, orderId } = await params;
  if (!ID.test(orderId)) notFound();
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  const scopes = tenantScopes(me, context.tenant_id);
  if (!context.features.checkout || !scopes.can("orders:read")) notFound();
  const detail = await api<OrderDetail>(`/admin/tenants/${tenantId}/orders/${orderId}`);
  const { order, customer } = detail;
  // Só pedido de transportadora tem remessa. Perguntar para os outros seria uma chamada por
  // pedido de retirada para receber `null` — e o operador de balcão paga o tempo dela.
  const porTransportadora = order.fulfillment_type === "shipping";
  const shipment = porTransportadora
    ? await api<Shipment | null>(`/admin/tenants/${tenantId}/orders/${orderId}/shipment`).catch(
        () => null,
      )
    : null;
  // Despachar compra etiqueta: gasta dinheiro da loja, e quem move pedido é quem pode gastar.
  const podeDespachar =
    porTransportadora &&
    scopes.can("orders:transition") &&
    ["accepted", "in_production"].includes(order.status) &&
    // `creating` também: despacho por volume interrompido no meio (processo caiu) é retomado
    // pelo servidor depois de 10 minutos parado; antes disso ele responde "em andamento".
    (shipment === null || shipment.status === "failed" || shipment.status === "creating");
  // Antes de comprar, quanto a etiqueta custa hoje (cotação em cache de 15 min no servidor).
  // Sem resposta, o botão continua: a prévia informa, não trava.
  const previa = podeDespachar
    ? await api<ShipmentPreview>(`/admin/tenants/${tenantId}/orders/${orderId}/shipment/preview`).catch(
        () => null,
      )
    : null;
  const plano = porTransportadora ? frozenPlanOf(order.fulfillment) : null;
  const volumes = shipment?.parcels ?? [];
  // Uma etiqueta por volume (Correios): cada volume tem a sua. Multivolume tem uma só, no topo.
  const porVolume = volumes.length > 1 && volumes.some((v) => v.label_url);
  const currency = order.currency;
  const when = (iso: string) =>
    new Intl.DateTimeFormat("pt-BR", {
      dateStyle: "short",
      timeStyle: "short",
      timeZone: context.timezone,
    }).format(new Date(iso));
  const place = order.fulfillment ? String(order.fulfillment.name ?? "") : "";
  const canRefund = scopes.can("payments:refund_request");
  const canDecide = scopes.can("payments:refund_approve");
  const refundable = order.paid_at && detail.payments.some((p) => p.status === "approved");
  const moves = detail.allowed_transitions.filter((t) => t !== "cancelled");
  const canCancel = detail.allowed_transitions.includes("cancelled");
  const risks = detail.risk_flags ? Object.keys(detail.risk_flags) : [];
  const statusLabel = ORDER_STATUS_LABEL[order.status] ?? order.status;
  const hidden = (
    <>
      <input type="hidden" name="tenant_id" value={tenantId} />
      <input type="hidden" name="order_id" value={orderId} />
      <input type="hidden" name="version" value={detail.version} />
    </>
  );

  const situation: { label: string; value: ReactNode }[] = [{ label: "Feito em", value: when(order.placed_at) }];
  if (order.status === "awaiting_payment" && order.expires_at)
    situation.push({ label: "Pagar até", value: when(order.expires_at) });
  if (order.cancelled_at) situation.push({ label: "Cancelado em", value: when(order.cancelled_at) });

  const person: { label: string; value: ReactNode }[] = [{ label: "Nome", value: customer.name ?? "—" }];
  if (customer.phone) person.push({ label: "Telefone", value: <a href={`tel:${customer.phone}`}>{customer.phone}</a> });
  if (customer.email) person.push({ label: "E-mail", value: <a href={`mailto:${customer.email}`}>{customer.email}</a> });

  const receiving: { label: string; value: ReactNode }[] = [
    {
      label: "Como recebe",
      value:
        order.fulfillment_type === "pickup"
          ? `Retirada em ${place}`
          : order.fulfillment_type === "delivery"
            ? `Entrega: ${place}`
            : order.fulfillment_type === "shipping"
              ? `Transportadora: ${[order.fulfillment?.carrier, order.fulfillment?.service_name].filter(Boolean).join(" ") || "—"}`
              : order.fulfillment_type === "none"
                ? "Sem entrega"
                : order.fulfillment_type,
    },
  ];
  if (order.fulfillment_type === "shipping") {
    const destino = (order.fulfillment?.address ?? null) as Record<string, string | null> | null;
    if (destino?.street) {
      const cep = String(destino.postal_code ?? "").replace(/^(\d{5})(\d{3})$/, "$1-$2");
      receiving.push({
        label: "Endereço",
        value: `${destino.street}, ${destino.number ?? "s/n"}${destino.complement ? ` (${destino.complement})` : ""} — ${destino.district ?? ""}, ${destino.city ?? ""}/${destino.state ?? ""} · CEP ${cep}`,
      });
    }
  }
  if (order.scheduled_start) receiving.push({ label: "Horário", value: when(order.scheduled_start) });

  return (
    <>
      <PageHeader
        eyebrow="Pedidos"
        title={`Pedido #${order.number} — ${statusLabel}`}
        lead="Confira os itens e use os botões de próximo passo conforme o pedido anda."
        actions={
          <Link href={`/t/${tenantId}/pedidos`} className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
            ← Todos os pedidos
          </Link>
        }
      />
      <Flash ok={ok} erro={erro} />
      {risks.length ? (
        <p className={styles.error}>Atenção: {risks.map((k) => RISK_LABEL[k] ?? k).join(", ")}.</p>
      ) : null}

      <div className={`${styles.split} ${local.layout}`}>
        <div>
          <Section
            title="Itens"
            description={`${order.items.length} ${order.items.length === 1 ? "item" : "itens"}`}
          >
            {detail.notes ? <p className={styles.note}>Observação do cliente: {detail.notes}</p> : null}
            <TableWrap>
              <table className={styles.table}>
                <thead>
                  <tr>
                    <th scope="col">Item</th>
                    <th scope="col" className={styles.num}>
                      Valor
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {order.items.map((item) => (
                    <tr key={item.line_no}>
                      <td>
                        <strong>
                          {item.quantity} × {item.name}
                        </strong>
                        {item.modifiers.length ? (
                          <span className={local.itemExtra}>{item.modifiers.map((m) => m.name).join(", ")}</span>
                        ) : null}
                        {item.event?.lot_name ? (
                          <span className={local.itemExtra}>Ingresso: {item.event.lot_name}</span>
                        ) : null}
                      </td>
                      <td className={styles.num}>{money(item.total_cents, currency)}</td>
                    </tr>
                  ))}
                </tbody>
                <tfoot className={local.totals}>
                  {order.delivery_fee_cents || order.discount_cents ? (
                    <tr>
                      <td>Subtotal</td>
                      <td className={styles.num}>{money(order.subtotal_cents, currency)}</td>
                    </tr>
                  ) : null}
                  {order.delivery_fee_cents ? (
                    <tr>
                      <td>Entrega</td>
                      <td className={styles.num}>{money(order.delivery_fee_cents, currency)}</td>
                    </tr>
                  ) : null}
                  {order.discount_cents ? (
                    <tr>
                      <td>Desconto</td>
                      <td className={styles.num}>−{money(order.discount_cents, currency)}</td>
                    </tr>
                  ) : null}
                  <tr className={local.grandTotal}>
                    <td>Total</td>
                    <td className={styles.num}>{money(order.total_cents, currency)}</td>
                  </tr>
                </tfoot>
              </table>
            </TableWrap>
          </Section>

          {detail.refunds.length || (canRefund && refundable) ? (
            <Section title="Devoluções" description="Dinheiro devolvido ao cliente neste pedido">
              {detail.refunds.length ? (
                <ul className={`${styles.rows} ${local.list}`}>
                  {detail.refunds.map((refund) => (
                    <li key={refund.id} className={styles.row}>
                      <div className={styles.rowMain}>
                        <div className={styles.host}>{money(refund.amount_cents, currency)}</div>
                        <p className={styles.rowSub}>
                          Por {REFUND_KIND_LABEL[refund.kind] ?? refund.kind} · {when(refund.requested_at)}
                          {refund.reason ? ` — ${refund.reason}` : ""}
                        </p>
                      </div>
                      <div className={styles.rowBadges}>
                        <Pill state={stateOf(REFUND_STATE, refund.status)}>
                          {REFUND_STATUS_LABEL[refund.status] ?? refund.status}
                        </Pill>
                      </div>
                      {canDecide && refund.status === "requested" ? (
                        <div className={`${styles.rowDetail} ${local.stackForm}`}>
                          <form action={decideRefund}>
                            {hidden}
                            <input type="hidden" name="refund_id" value={refund.id} />
                            <input type="hidden" name="decision" value="approve" />
                            <button type="submit" className={styles.button}>
                              Aprovar devolução
                            </button>
                          </form>
                          <form action={decideRefund} className={styles.toolbar}>
                            {hidden}
                            <input type="hidden" name="refund_id" value={refund.id} />
                            <input type="hidden" name="decision" value="reject" />
                            <label className={styles.formGrow}>
                              Motivo da recusa
                              <input name="reason" maxLength={200} placeholder="motivo da recusa" />
                            </label>
                            <button type="submit" className={styles.buttonDanger}>
                              Recusar
                            </button>
                          </form>
                        </div>
                      ) : null}
                      {canDecide && refund.status === "approved" && refund.method === "external" ? (
                        <form action={decideRefund} className={`${styles.rowDetail} ${styles.toolbar}`}>
                          {hidden}
                          <input type="hidden" name="refund_id" value={refund.id} />
                          <input type="hidden" name="decision" value="complete" />
                          <label className={styles.formGrow}>
                            Como devolveu (fica registrado)
                            <input
                              name="evidence"
                              maxLength={500}
                              placeholder="ex.: Pix devolvido no app, comprovante 123"
                            />
                          </label>
                          <button type="submit" className={styles.button}>
                            Registrar devolução feita
                          </button>
                        </form>
                      ) : null}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className={styles.hint}>Nenhuma devolução.</p>
              )}
              {canRefund && refundable ? (
                <details className={local.more}>
                  <summary>Devolver dinheiro ao cliente</summary>
                  <form action={requestRefund}>
                    {hidden}
                    <fieldset className={local.plain}>
                      <legend className={styles.hint}>
                        Marque o que volta. O valor de cada item é calculado do que o cliente
                        pagou por ele — com desconto no pedido, a linha vale menos que o preço
                        de etiqueta.
                      </legend>
                      {order.items.map((item) => (
                        <div key={item.line_no} className={styles.check}>
                          <label>
                            <input type="checkbox" name="refund_line" value={item.line_no} />
                            <span>
                              {item.name}
                              <span className={styles.fieldHint}>
                                {item.quantity} {item.unit_label} · {money(item.total_cents, currency)}
                              </span>
                            </span>
                          </label>
                          <input
                            name={`refund_qty_${item.line_no}`}
                            type="number"
                            min={0}
                            step="any"
                            max={Number(item.quantity)}
                            defaultValue={item.quantity}
                            aria-label={`Quantidade a devolver de ${item.name}`}
                          />
                        </div>
                      ))}
                      <label className={styles.check}>
                        <input type="checkbox" name="restock" defaultChecked />
                        <span>
                          Devolver ao estoque o que voltou
                          <span className={styles.fieldHint}>
                            Desmarque se o item voltou quebrado ou não voltou.
                          </span>
                        </span>
                      </label>
                    </fieldset>
                    <div className={styles.fields}>
                      <label className={styles.field}>
                        Ou um valor (R$)
                        <input name="amount" inputMode="decimal" placeholder="ex.: 12,50" />
                        <span className={styles.fieldHint}>
                          Sem item marcado. Em branco: devolve tudo o que ainda resta.
                        </span>
                      </label>
                      <label className={`${styles.field} ${styles.fieldWide}`}>
                        Motivo da devolução
                        <input name="reason" maxLength={200} required />
                      </label>
                    </div>
                    <div className={styles.formActions}>
                      <button type="submit" className={styles.button}>
                        Devolver dinheiro
                      </button>
                    </div>
                  </form>
                </details>
              ) : null}
            </Section>
          ) : null}

          {plano ? <PackingList plan={plano} orderNumber={order.number} /> : null}

          {porTransportadora ? (
            <Section
              title="Envio"
              description="Comprar a etiqueta marca o pedido como enviado e manda o código ao cliente"
            >
              {shipment ? (
                <>
                  <div className={local.pills}>
                    <Pill state={stateOf(SHIPMENT_STATE, shipment.status)}>
                      {SHIPMENT_STATUS_LABEL[shipment.status] ?? shipment.status}
                    </Pill>
                  </div>
                  <KeyValues
                    items={[
                      {
                        label: "Transportadora",
                        value: `${shipment.carrier} ${shipment.service_name}`.trim() || "—",
                      },
                      {
                        label: "Rastreio",
                        value: porVolume
                          ? `Um código por volume (${volumes.length}), abaixo`
                          : (shipment.tracking_code ?? "Ainda não veio"),
                      },
                      {
                        label: "Custo da etiqueta",
                        value:
                          shipment.cost_cents !== null
                            ? money(shipment.cost_cents, currency)
                            : "—",
                      },
                      ...(shipment.purchased_at
                        ? [{ label: "Comprada em", value: when(shipment.purchased_at) }]
                        : []),
                    ]}
                  />
                  {porVolume ? (
                    <ol className={local.labels} aria-label="Etiquetas por volume">
                      {volumes.map((v) => (
                        <li key={v.n}>
                          <div className={local.labelMain}>
                            <strong>Volume {v.n}</strong>
                            <span className={local.parcelMeta}>
                              {[weightLabel(v.weight_grams), v.tracking_code ?? "rastreio ainda não veio"].join(" · ")}
                            </span>
                          </div>
                          {v.status ? (
                            <Pill state={stateOf(SHIPMENT_STATE, v.status)}>
                              {SHIPMENT_STATUS_LABEL[v.status] ?? v.status}
                            </Pill>
                          ) : null}
                          {v.label_url ? (
                            <a href={v.label_url} target="_blank" rel="noopener noreferrer">
                              Imprimir etiqueta {v.n}
                            </a>
                          ) : (
                            <span className={styles.hint}>Etiqueta ainda não comprada</span>
                          )}
                        </li>
                      ))}
                    </ol>
                  ) : shipment.label_url ? (
                    <p>
                      <a href={shipment.label_url} target="_blank" rel="noopener noreferrer">
                        Imprimir etiqueta
                      </a>
                    </p>
                  ) : null}
                  {shipment.last_error ? (
                    <p className={styles.note}>{shipment.last_error}</p>
                  ) : null}
                  {shipment.events.length ? (
                    <ul className={styles.steps}>
                      {shipment.events.map((evento) => (
                        <li key={`${evento.occurred_at}-${evento.status}`}>
                          {when(evento.occurred_at)} — {evento.description || evento.status}
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </>
              ) : (
                <p className={styles.hint}>
                  Nenhuma etiqueta comprada ainda.
                </p>
              )}
              {podeDespachar && previa ? (
                <div className={local.preview} role="status">
                  {previa.price_cents !== null ? (
                    <p>
                      Custo agora: <strong>{money(previa.price_cents, currency)}</strong>
                      {previa.labels > 1 ? ` em ${previa.labels} etiquetas` : ""} · cliente pagou{" "}
                      {previa.charged_cents ? money(previa.charged_cents, currency) : "nada (frete grátis)"}
                    </p>
                  ) : (
                    <p className={styles.hint}>
                      {SHIPMENT_PREVIEW_PROBLEM[previa.problem ?? ""] ?? "Não deu para ver o custo agora."}{" "}
                      Dá para tentar despachar mesmo assim.
                    </p>
                  )}
                  {previa.needs_confirmation ? (
                    <p className={styles.error}>
                      O frete subiu {previa.increase_percent}% desde que o cliente comprou.
                    </p>
                  ) : null}
                </div>
              ) : null}
              {podeDespachar ? (
                <form action={dispatchShipment} className={local.actions}>
                  <input type="hidden" name="tenant_id" value={tenantId} />
                  <input type="hidden" name="order_id" value={orderId} />
                  {previa?.needs_confirmation ? (
                    <>
                      <input type="hidden" name="cost_check" value="1" />
                      <label className={styles.check}>
                        <input type="checkbox" name="confirm_cost" required /> Comprar mesmo com o frete mais
                        caro
                      </label>
                    </>
                  ) : null}
                  <button type="submit" className={styles.button}>
                    {shipment?.status === "failed"
                      ? "Tentar despachar de novo"
                      : shipment?.status === "creating"
                        ? "Retomar o despacho"
                        : "Despachar agora"}
                  </button>
                </form>
              ) : null}
              {porTransportadora && !podeDespachar && !shipment ? (
                <p className={styles.hint}>
                  {["accepted", "in_production"].includes(order.status)
                    ? "Seu papel não permite despachar."
                    : "Aceite o pedido antes de despachar."}
                </p>
              ) : null}
            </Section>
          ) : null}

          <Section title="Andamento">
            {order.timeline.length ? (
              <ol className={local.timeline}>
                {order.timeline.map((event, i) => (
                  <li key={i}>
                    <span className={local.timelineWhen}>{when(event.at)}</span>
                    <strong>{ORDER_STATUS_LABEL[event.status] ?? event.status}</strong>
                    {event.reason ? <span className="muted"> — {event.reason}</span> : null}
                  </li>
                ))}
              </ol>
            ) : (
              <p className={styles.hint}>Nenhuma mudança registrada ainda.</p>
            )}
          </Section>

          <Section title="E-mails ao cliente">
            {detail.emails.length === 0 ? (
              <p className={styles.hint}>Nenhum e-mail para este pedido.</p>
            ) : (
              <ul className={`${styles.rows} ${local.list}`}>
                {detail.emails.map((email) => (
                  <li key={email.id} className={styles.row}>
                    <div className={styles.rowMain}>
                      <strong>{email.subject}</strong>
                      <p className={styles.rowSub}>
                        Para {email.recipient}
                        {email.sent_at ? ` · ${when(email.sent_at)}` : ""}
                      </p>
                      {email.last_error ? <p className={styles.rowSub}>{email.last_error}</p> : null}
                    </div>
                    <div className={styles.rowBadges}>
                      <Pill state={EMAIL_STATE[email.status] ?? "off"}>
                        {DELIVERY_STATUS_LABEL[email.status] ?? email.status}
                      </Pill>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </div>

        <aside className={local.aside}>
          <Section title="Situação">
            <div className={local.pills}>
              <Pill state={stateOf(ORDER_STATE, order.status)}>{statusLabel}</Pill>
              {order.refund_status !== "none" ? <Pill state="info">Com devolução</Pill> : null}
            </div>
            <KeyValues items={situation} />
            {moves.length ? (
              <div className={local.actions}>
                {moves.map((target, i) => (
                  <form key={target} action={moveOrder}>
                    {hidden}
                    <input type="hidden" name="to" value={target} />
                    {/* The first allowed step is the natural next one; the others skip ahead. */}
                    <button type="submit" className={i === 0 ? styles.button : styles.buttonGhost}>
                      {TRANSITION_LABEL[target] ?? target}
                    </button>
                  </form>
                ))}
              </div>
            ) : (
              <p className={styles.hint}>
                {order.status === "awaiting_payment"
                  ? "Esperando o cliente pagar. O pedido anda sozinho quando o pagamento for aprovado."
                  : "Nada para você fazer neste pedido agora."}
              </p>
            )}
            {canCancel ? (
              <form action={moveOrder} className={`${local.danger} ${local.stackForm}`}>
                {hidden}
                <input type="hidden" name="to" value="cancelled" />
                <label className={styles.field}>
                  Motivo do cancelamento
                  <input name="reason" maxLength={200} placeholder="ex.: sem ingrediente" />
                  <span className={styles.fieldHint}>Fica registrado no andamento do pedido.</span>
                </label>
                <label className={styles.check}>
                  <input type="checkbox" name="restock" defaultChecked /> Devolver os itens ao estoque
                </label>
                <button type="submit" className={styles.buttonDanger}>
                  Cancelar pedido{order.paid_at ? " e devolver o dinheiro" : ""}
                </button>
              </form>
            ) : null}
          </Section>

          <Section title="Cliente">
            <KeyValues items={person} />
          </Section>

          <Section title={order.fulfillment_type === "pickup" ? "Retirada" : "Entrega"}>
            <KeyValues items={receiving} />
          </Section>

          <Section title="Pagamento" description={order.paid_at ? `Pago em ${when(order.paid_at)}` : "Ainda não pago"}>
            {detail.payments.length === 0 ? (
              <p className={styles.hint}>Nenhuma tentativa de pagamento ainda.</p>
            ) : (
              <ul className={`${styles.rows} ${local.list}`}>
                {detail.payments.map((payment) => (
                  <li key={payment.id} className={styles.row}>
                    <div className={styles.rowMain}>
                      <strong>
                        {PAYMENT_PROVIDERS[payment.provider]?.label ?? payment.provider} ·{" "}
                        {PAYMENT_METHOD_LABEL[payment.method] ?? payment.method}
                        {payment.card?.last_four ? ` (final ${payment.card.last_four})` : ""}
                      </strong>
                      <p className={styles.rowSub}>
                        {when(payment.created_at)}
                        {payment.installments > 1 ? ` · em ${payment.installments}x` : ""}
                      </p>
                      {payment.failure_code ? <p className={styles.rowSub}>{payment.failure_code}</p> : null}
                    </div>
                    <div className={styles.rowBadges}>
                      <Pill state={stateOf(PAYMENT_STATE, payment.status)}>
                        {PAYMENT_STATUS_LABEL[payment.status] ?? payment.status}
                      </Pill>
                      <span className={local.money}>{money(payment.amount_cents, currency)}</span>
                    </div>
                  </li>
                ))}
              </ul>
            )}
          </Section>
        </aside>
      </div>
    </>
  );
}
