import type { Metadata } from "next";
import Link from "next/link";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { type Order, orderStatusLabel } from "@/lib/orders";
import { getStorefrontContext } from "@/lib/server-context";
import { formatPrice } from "@/lib/storefront";

import { StoreShell } from "../../../_store/store-shell";
import styles from "../../../_store/store.module.css";

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
  searchParams: Promise<{ ok?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { id } = await params;
  if (!ID.test(id)) notFound();
  const back = `/conta/pedidos/${id}`;
  if (!(await cookies()).get(CUSTOMER_SESSION_COOKIE)) redirect(`/entrar?next=${encodeURIComponent(back)}`);
  let order: Order;
  try {
    order = await customerApi<Order>(`/checkout/orders/${id}`);
  } catch (error) {
    if (error instanceof CustomerApiError && error.status === 401) redirect(`/entrar?next=${encodeURIComponent(back)}`);
    if (error instanceof CustomerApiError && error.status === 404) notFound();
    throw error;
  }
  const { ok } = await searchParams;
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
      {order.status === "awaiting_payment" ? <p className="muted">O pagamento online chega em breve nesta loja.</p> : null}
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
