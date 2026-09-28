import type { Metadata } from "next";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { type Order, orderStatusLabel } from "@/lib/orders";
import { isPaymentErrorCode, type OrderPayment, paymentError } from "@/lib/payments";
import { getStorefrontContext } from "@/lib/server-context";
import { formatPrice } from "@/lib/storefront";

import { StoreShell } from "../../../_store/store-shell";
import { Breadcrumb, Notice, PageHead, Pill, Section, Split } from "../../../_store/ui";
import styles from "../../../_store/store.module.css";
import { CUSTOMER_ORDER_STATE, stateOf } from "@/lib/store/states";
import { cancelOrder } from "../../actions";
import { PaymentSection } from "./payment-section";

export const metadata: Metadata = { title: "Pedido", robots: { index: false, follow: false } };

const ID = /^[0-9a-f-]{36}$/;

function money(cents: number, currency: string): string {
  return formatPrice({ amount_cents: cents, compare_at_cents: null, promo_active: false, promo_ends_at: null, currency });
}

export default async function OrderPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { id } = await params;
  if (!ID.test(id)) notFound();
  const back = `/conta/pedidos/${id}`;
  if (!(await cookies()).get(CUSTOMER_SESSION_COOKIE)) redirect(`/entrar?next=${encodeURIComponent(back)}`);
  let order: Order;
  try {
    order = await customerApi<Order>(`/me/orders/${id}`);
  } catch (error) {
    if (error instanceof CustomerApiError && error.status === 401) redirect(`/entrar?next=${encodeURIComponent(back)}`);
    if (error instanceof CustomerApiError && error.status === 404) notFound();
    throw error;
  }
  // The payment part needs the store's `checkout` module; without it the order page still works.
  let payment: OrderPayment | null = null;
  try {
    payment = await customerApi<OrderPayment>(`/checkout/orders/${id}/payment`);
  } catch (error) {
    if (!(error instanceof CustomerApiError) || error.status >= 500) throw error;
  }
  const { ok, erro } = await searchParams;
  const zone = context.tenant.timezone;
  const when = (iso: string) =>
    new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: zone }).format(new Date(iso));
  const place = order.fulfillment ? String(order.fulfillment.name ?? "") : "";
  const instructions = order.fulfillment ? String(order.fulfillment.instructions ?? "") : "";

  const cancelavel = ["awaiting_payment", "payment_confirmed", "accepted"].includes(order.status);

  return (
    <StoreShell context={context}>
      <Breadcrumb
        trail={[
          { name: "Minha conta", href: "/conta" },
          { name: "Meus pedidos", href: "/conta/pedidos" },
          { name: `Pedido #${order.number}` },
        ]}
      />
      {ok === "pedido" ? (
        <Notice kind="ok">
          <strong>Pedido recebido!</strong> A loja já foi avisada.
        </Notice>
      ) : null}
      {ok === "cancelado" ? <Notice kind="info">Pedido cancelado.</Notice> : null}
      {ok === "trocar" ? <Notice kind="info">Pagamento cancelado. Escolha outro jeito de pagar.</Notice> : null}
      {ok === "retorno" ? <Notice kind="info">Você voltou do pagamento. A confirmação aparece aqui.</Notice> : null}
      {erro && isPaymentErrorCode(erro) ? (
        <Notice kind="error">{paymentError(erro)}</Notice>
      ) : erro === "cancel_window_closed" ? (
        <Notice kind="error">Este pedido não pode mais ser cancelado por aqui. Fale com a loja.</Notice>
      ) : erro ? (
        <Notice kind="error">Não foi possível cancelar. Tente de novo.</Notice>
      ) : null}

      <PageHead
        title={`Pedido #${order.number}`}
        actions={<Pill state={stateOf(CUSTOMER_ORDER_STATE, order.status)}>{orderStatusLabel(order.status)}</Pill>}
        lead={
          order.status === "awaiting_payment" && order.expires_at
            ? `Pague até ${when(order.expires_at)} para a loja separar o seu pedido.`
            : undefined
        }
      />

      <Split
        aside={
          <div className={styles.summary} aria-label="Resumo do pedido">
            <h2>Resumo</h2>
            <ul className={styles.reviewItems}>
              {order.items.map((item) => (
                <li key={item.line_no}>
                  <span>
                    {item.quantity} × {item.name}
                    {item.modifiers.length ? (
                      <span className="muted"> ({item.modifiers.map((m) => m.name).join(", ")})</span>
                    ) : null}
                    {item.event?.lot_name ? <span className="muted"> (ingresso: {item.event.lot_name})</span> : null}
                  </span>
                  <span className={styles.cartSubtotal}>{money(item.total_cents, order.currency)}</span>
                </li>
              ))}
            </ul>
            <dl className={styles.totals}>
              {order.delivery_fee_cents ? (
                <div>
                  <dt>Entrega</dt>
                  <dd>{money(order.delivery_fee_cents, order.currency)}</dd>
                </div>
              ) : null}
              {order.discount_cents ? (
                <div>
                  <dt>Desconto</dt>
                  <dd className={styles.discount}>−{money(order.discount_cents, order.currency)}</dd>
                </div>
              ) : null}
              <div className={styles.totalRow}>
                <dt>Total</dt>
                <dd>{money(order.total_cents, order.currency)}</dd>
              </div>
            </dl>
            {order.fulfillment_type === "pickup" ? <p className={styles.where}>Retirada: {place}</p> : null}
            {order.fulfillment_type === "delivery" ? <p className={styles.where}>Entrega: {place}</p> : null}
            {order.scheduled_start ? <p className={styles.where}>Horário: {when(order.scheduled_start)}</p> : null}
            {instructions ? <p className="muted">{instructions}</p> : null}
          </div>
        }
      >
        {payment ? <PaymentSection state={payment} when={when} /> : null}

        <Section title="Andamento" variant="card">
          <ol className={styles.timeline}>
            {order.timeline.map((event, i) => (
              <li key={i} data-current={i === order.timeline.length - 1 ? "true" : undefined}>
                <strong>{orderStatusLabel(event.status)}</strong>
                <span className="muted">{when(event.at)}</span>
              </li>
            ))}
          </ol>
          {cancelavel ? (
            <form action={cancelOrder} className={styles.cancelRow}>
              <input type="hidden" name="order_id" value={order.id} />
              <button type="submit" className={styles.linkButton}>
                Cancelar pedido
              </button>
            </form>
          ) : null}
        </Section>
      </Split>
    </StoreShell>
  );
}
