import type { Metadata } from "next";
import Link from "next/link";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { orderStatusLabel } from "@/lib/orders";
import { getStorefrontContext } from "@/lib/server-context";
import { formatPrice } from "@/lib/storefront";

import { StoreShell } from "../../_store/store-shell";
import styles from "../../_store/store.module.css";

export const metadata: Metadata = { title: "Meus pedidos", robots: { index: false, follow: false } };

interface OrderPage {
  items: { id: string; number: number; status: string; total_cents: number; currency: string; placed_at: string }[];
  next_cursor: string | null;
}

export default async function OrdersPage({ searchParams }: { searchParams: Promise<{ cursor?: string }> }) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  if (!(await cookies()).get(CUSTOMER_SESSION_COOKIE)) redirect("/entrar?next=%2Fconta%2Fpedidos");
  const { cursor } = await searchParams;
  const query = cursor ? `?cursor=${encodeURIComponent(cursor.slice(0, 256))}` : "";
  let page: OrderPage;
  try {
    page = await customerApi<OrderPage>(`/me/orders${query}`);
  } catch (error) {
    if (error instanceof CustomerApiError && error.status === 401) redirect("/entrar?next=%2Fconta%2Fpedidos");
    throw error;
  }
  const when = (iso: string) =>
    new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeZone: context.tenant.timezone }).format(new Date(iso));

  return (
    <StoreShell context={context}>
      <p>
        <Link href="/conta">← Minha conta</Link>
      </p>
      <h1>Meus pedidos</h1>
      {page.items.length === 0 ? <p>Você ainda não fez pedidos nesta loja.</p> : null}
      <table className={styles.lots}>
        <tbody>
          {page.items.map((order) => (
            <tr key={order.id}>
              <th scope="row">
                <Link href={`/conta/pedidos/${order.id}`}>Pedido #{order.number}</Link>
              </th>
              <td>{when(order.placed_at)}</td>
              <td>{orderStatusLabel(order.status)}</td>
              <td>
                {formatPrice({
                  amount_cents: order.total_cents,
                  compare_at_cents: null,
                  promo_active: false,
                  promo_ends_at: null,
                  currency: order.currency,
                })}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {page.next_cursor ? (
        <p>
          <Link href={`/conta/pedidos?cursor=${encodeURIComponent(page.next_cursor)}`}>Pedidos anteriores →</Link>
        </p>
      ) : null}
    </StoreShell>
  );
}
