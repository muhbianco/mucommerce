import type { Metadata } from "next";
import Link from "next/link";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { type Order, orderStatusLabel } from "@/lib/orders";
import { isPaymentErrorCode, type OrderPayment, paymentError } from "@/lib/payments";
import { getStorefrontContext } from "@/lib/server-context";
import { formatPrice } from "@/lib/storefront";

import { StoreShell } from "../../../_store/store-shell";
import styles from "../../../_store/store.module.css";
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

  return (
    <StoreShell context={context}>
      <p>
        <Link href="/conta">← Minha conta</Link>
      </p>
      <h1>Pedido #{order.number}</h1>
      {ok === "pedido" ? <p role="status">Pedido recebido!</p> : null}
      {ok === "cancelado" ? <p role="status">Pedido cancelado.</p> : null}
      {ok === "trocar" ? <p role="status">Pagamento cancelado. Escolha outro jeito de pagar.</p> : null}
      {ok === "retorno" ? <p role="status">Você voltou do pagamento. A confirmação aparece aqui.</p> : null}
      {erro && isPaymentErrorCode(erro) ? (
        <p role="alert">{paymentError(erro)}</p>
      ) : erro === "cancel_window_closed" ? (
        <p role="alert">Este pedido não pode mais ser cancelado por aqui. Fale com a loja.</p>
      ) : erro ? (
        <p role="alert">Não foi possível cancelar. Tente de novo.</p>
      ) : null}
      <p>
        <strong>{orderStatusLabel(order.status)}</strong>
        {order.status === "awaiting_payment" && order.expires_at ? ` — pague até ${when(order.expires_at)}` : ""}
      </p>
      <table className={styles.lots}>
        <tbody>
          {order.items.map((item) => (
            <tr key={item.line_no}>
              <th scope="row">
                {item.quantity} × {item.name}
                {item.modifiers.length ? <div className="muted">{item.modifiers.map((m) => m.name).join(", ")}</div> : null}
                {item.event?.lot_name ? <div className="muted">Ingresso: {item.event.lot_name}</div> : null}
              </th>
              <td>{money(item.total_cents, order.currency)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {order.delivery_fee_cents ? <p>Entrega: {money(order.delivery_fee_cents, order.currency)}</p> : null}
      {order.discount_cents ? <p>Desconto: −{money(order.discount_cents, order.currency)}</p> : null}
      <p>
        <strong>Total: {money(order.total_cents, order.currency)}</strong>
      </p>
      {order.fulfillment_type === "pickup" ? <p>Retirada: {place}</p> : null}
      {order.fulfillment_type === "delivery" ? <p>Entrega: {place}</p> : null}
      {order.scheduled_start ? <p>Horário: {when(order.scheduled_start)}</p> : null}
      {payment ? <PaymentSection state={payment} when={when} /> : null}
      {["awaiting_payment", "payment_confirmed", "accepted"].includes(order.status) ? (
        <form action={cancelOrder}>
          <input type="hidden" name="order_id" value={order.id} />
          <button type="submit" className="muted">
            Cancelar pedido
          </button>
        </form>
      ) : null}
      <h2>Andamento</h2>
      <ul>
        {order.timeline.map((event, i) => (
          <li key={i}>
            {when(event.at)} — {orderStatusLabel(event.status)}
          </li>
        ))}
      </ul>
    </StoreShell>
  );
}
