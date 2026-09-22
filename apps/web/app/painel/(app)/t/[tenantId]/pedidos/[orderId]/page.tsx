import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import {
  DELIVERY_STATUS_LABEL,
  ORDER_STATUS_LABEL,
  type OrderDetail,
  PAYMENT_STATUS_LABEL,
  REFUND_KIND_LABEL,
  REFUND_STATUS_LABEL,
  TRANSITION_LABEL,
} from "@/lib/panel/types";

import styles from "../../../../../panel.module.css";
import { Flash } from "../../flash";
import { decideRefund, moveOrder, requestRefund } from "../actions";

export const metadata: Metadata = { title: "Pedido" };

const ID = /^[0-9a-f-]{36}$/;
const RISK_LABEL: Record<string, string> = {
  late_payment: "pagamento chegou fora do prazo",
  duplicate_payment: "pagamento em dobro",
  chargeback: "contestação no cartão",
  late_payment_refunded: "pagamento fora do prazo devolvido",
  duplicate_payment_refunded: "pagamento em dobro devolvido",
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
  const { order } = detail;
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
  const hidden = (
    <>
      <input type="hidden" name="tenant_id" value={tenantId} />
      <input type="hidden" name="order_id" value={orderId} />
      <input type="hidden" name="version" value={detail.version} />
    </>
  );

  return (
    <>
      <p>
        <Link href={`/t/${tenantId}/pedidos`}>← Pedidos</Link>
      </p>
      <h2>
        Pedido #{order.number} — {ORDER_STATUS_LABEL[order.status] ?? order.status}
      </h2>
      <Flash ok={ok} erro={erro} />
      {detail.risk_flags && Object.keys(detail.risk_flags).length ? (
        <p className={styles.error}>
          Atenção: {Object.keys(detail.risk_flags).map((k) => RISK_LABEL[k] ?? k).join(", ")}.
        </p>
      ) : null}

      <section className={styles.card}>
        <h2>Itens</h2>
        <table className={styles.table}>
          <tbody>
            {order.items.map((item) => (
              <tr key={item.line_no}>
                <th scope="row">
                  {item.quantity} × {item.name}
                  {item.modifiers.length ? (
                    <div className="muted">{item.modifiers.map((m) => m.name).join(", ")}</div>
                  ) : null}
                  {item.event?.lot_name ? <div className="muted">Ingresso: {item.event.lot_name}</div> : null}
                </th>
                <td>{money(item.total_cents, currency)}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {order.delivery_fee_cents ? <p>Entrega: {money(order.delivery_fee_cents, currency)}</p> : null}
        {order.discount_cents ? <p>Desconto: −{money(order.discount_cents, currency)}</p> : null}
        <p>
          <strong>Total: {money(order.total_cents, currency)}</strong>
          {order.refund_status !== "none" ? " · com devolução" : ""}
        </p>
        <p className="muted">
          {detail.customer.name ?? "Cliente"}
          {detail.customer.phone ? ` · ${detail.customer.phone}` : ""} ·{" "}
          {order.fulfillment_type === "pickup" ? `retirada em ${place}` : null}
          {order.fulfillment_type === "delivery" ? `entrega: ${place}` : null}
          {order.fulfillment_type === "none" ? "sem entrega" : null}
          {order.scheduled_start ? ` · ${when(order.scheduled_start)}` : ""}
        </p>
        {detail.notes ? <p>Observação do cliente: {detail.notes}</p> : null}
      </section>

      {moves.length || detail.allowed_transitions.includes("cancelled") ? (
        <section className={styles.card}>
          <h2>Ações</h2>
          <div className={styles.form}>
            {moves.map((target) => (
              <form key={target} action={moveOrder}>
                {hidden}
                <input type="hidden" name="to" value={target} />
                <button type="submit">{TRANSITION_LABEL[target] ?? target}</button>
              </form>
            ))}
          </div>
          {detail.allowed_transitions.includes("cancelled") ? (
            <form action={moveOrder} className={styles.form}>
              {hidden}
              <input type="hidden" name="to" value="cancelled" />
              <label>
                Motivo do cancelamento
                <input name="reason" maxLength={200} placeholder="ex.: sem ingrediente" />
              </label>
              <label>
                <input type="checkbox" name="restock" defaultChecked /> Devolver os itens ao estoque
              </label>
              <button type="submit" className="muted">
                Cancelar pedido{order.paid_at ? " e devolver o dinheiro" : ""}
              </button>
            </form>
          ) : null}
        </section>
      ) : null}

      <section className={styles.card}>
        <h2>Pagamentos</h2>
        {detail.payments.length === 0 ? (
          <p className="muted">Nenhuma tentativa de pagamento ainda.</p>
        ) : (
          <table className={styles.table}>
            <tbody>
              {detail.payments.map((payment) => (
                <tr key={payment.id}>
                  <th scope="row">
                    {payment.provider} · {payment.method}
                    {payment.card?.last_four ? ` (final ${payment.card.last_four})` : ""}
                    <div className="muted">{when(payment.created_at)}</div>
                  </th>
                  <td>
                    {PAYMENT_STATUS_LABEL[payment.status] ?? payment.status}
                    {payment.failure_code ? <div className="muted">{payment.failure_code}</div> : null}
                  </td>
                  <td>{money(payment.amount_cents, currency)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>

      <section className={styles.card}>
        <h2>Devoluções</h2>
        {detail.refunds.length === 0 ? <p className="muted">Nenhuma devolução.</p> : null}
        {detail.refunds.map((refund) => (
          <div key={refund.id} className={styles.form}>
            <p>
              {money(refund.amount_cents, currency)} · {REFUND_STATUS_LABEL[refund.status] ?? refund.status} ·{" "}
              {REFUND_KIND_LABEL[refund.kind] ?? refund.kind}
              <br />
              <span className="muted">
                {when(refund.requested_at)} · {refund.reason}
              </span>
            </p>
            {canDecide && refund.status === "requested" ? (
              <>
                <form action={decideRefund}>
                  {hidden}
                  <input type="hidden" name="refund_id" value={refund.id} />
                  <input type="hidden" name="decision" value="approve" />
                  <button type="submit">Aprovar devolução</button>
                </form>
                <form action={decideRefund}>
                  {hidden}
                  <input type="hidden" name="refund_id" value={refund.id} />
                  <input type="hidden" name="decision" value="reject" />
                  <input name="reason" maxLength={200} placeholder="motivo da recusa" />
                  <button type="submit" className="muted">
                    Recusar
                  </button>
                </form>
              </>
            ) : null}
            {canDecide && refund.status === "approved" && refund.method === "external" ? (
              <form action={decideRefund}>
                {hidden}
                <input type="hidden" name="refund_id" value={refund.id} />
                <input type="hidden" name="decision" value="complete" />
                <label style={{ flexGrow: 2 }}>
                  Como devolveu (fica registrado)
                  <input name="evidence" maxLength={500} placeholder="ex.: Pix devolvido no app, comprovante 123" />
                </label>
                <button type="submit">Registrar devolução feita</button>
              </form>
            ) : null}
          </div>
        ))}
        {canRefund && refundable ? (
          <form action={requestRefund} className={styles.form}>
            {hidden}
            <label>
              Valor (em branco: tudo o que resta)
              <input name="amount" inputMode="decimal" placeholder="ex.: 12,50" />
            </label>
            <label style={{ flexGrow: 2 }}>
              Motivo da devolução
              <input name="reason" maxLength={200} required />
            </label>
            <button type="submit">Devolver dinheiro</button>
          </form>
        ) : null}
      </section>

      <section className={styles.card}>
        <h2>Andamento</h2>
        <ul>
          {order.timeline.map((event, i) => (
            <li key={i}>
              {when(event.at)} — {ORDER_STATUS_LABEL[event.status] ?? event.status}
              {event.reason ? ` (${event.reason})` : ""}
            </li>
          ))}
        </ul>
        <h2>E-mails</h2>
        {detail.emails.length === 0 ? (
          <p className="muted">Nenhum e-mail para este pedido.</p>
        ) : (
          <ul>
            {detail.emails.map((email) => (
              <li key={email.id}>
                {email.subject} → {email.recipient} ·{" "}
                {DELIVERY_STATUS_LABEL[email.status] ?? email.status}
                {email.last_error ? <span className="muted"> ({email.last_error})</span> : null}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}
