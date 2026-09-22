import type { Metadata } from "next";
import Link from "next/link";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";
import { formatPrice, type StorePrice } from "@/lib/storefront";

import { CART_ERRORS } from "../_store/add-to-cart";
import { chooseFulfillment, removeCartItem, setCartQuantity } from "../_store/cart-actions";
import { StoreShell } from "../_store/store-shell";
import styles from "../_store/store.module.css";

export const metadata: Metadata = { title: "Carrinho", robots: { index: false, follow: false } };

interface CartItem {
  id: string;
  product_slug: string | null;
  product_kind: string | null;
  name: string;
  image_url: string | null;
  quantity: string;
  unit_label: string | null;
  modifiers: { id: string; name: string; price_cents: number }[];
  unit_price_cents: number | null;
  compare_at_cents: number | null;
  subtotal_cents: number | null;
  problem: { code: string; detail: Record<string, unknown> } | null;
}

interface Slot {
  date: string;
  start: string;
  end: string;
}

interface Cart {
  id: string | null;
  version: number;
  items: CartItem[];
  fulfillment: { type?: string; pickup_location_id?: string; address_id?: string; slot_date?: string; slot_start?: string } | null;
  quote: {
    subtotal_cents: number;
    discount_cents: number;
    delivery_fee_cents: number;
    total_cents: number;
    currency: string;
    needs_fulfillment: boolean;
    fulfillment: { type: string; fee_cents: number; snapshot: Record<string, unknown>; problems: string[] } | null;
    problems: number;
    can_checkout: boolean;
  };
  options: {
    modes: string[];
    pickup_locations: { id: string; name: string; address: string; instructions: string | null }[];
    delivery_zones: { id: string; name: string; fee_cents: number }[];
    addresses: { id: string; label: string | null; summary: string; is_default: boolean }[];
    slots: Record<string, Slot[]>;
  };
}

const PROBLEM: Record<string, string> = {
  unavailable: "Não está mais à venda. Tire do carrinho.",
  out_of_stock: "Sem estoque para essa quantidade.",
  invalid_modifiers: "Os adicionais mudaram; escolha de novo.",
  invalid_quantity: "Quantidade inválida.",
  lot_not_on_sale: "Este lote não está à venda agora.",
};

const FULFILLMENT_PROBLEM: Record<string, string> = {
  mode_unavailable: "Essa forma de receber não está disponível.",
  location_unknown: "Escolha um local de retirada.",
  address_required: "Escolha um endereço de entrega.",
  out_of_zone: "A loja não entrega nesse endereço.",
  below_minimum: "O pedido ainda não chegou ao valor mínimo.",
  slot_required: "Escolha um horário.",
  slot_invalid: "Esse horário não está mais disponível.",
};

function money(cents: number, currency: string): string {
  const price: StorePrice = { amount_cents: cents, compare_at_cents: null, promo_active: false, promo_ends_at: null, currency };
  return formatPrice(price);
}

function slotLabel(slot: Slot): string {
  const day = new Date(`${slot.date}T12:00:00`).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
  return `${day} ${slot.start}–${slot.end}`;
}

export default async function CartPage({ searchParams }: { searchParams: Promise<{ ok?: string; erro?: string }> }) {
  const context = await getStorefrontContext();
  if (!context || !context.features.checkout) notFound();
  if (!(await cookies()).get(CUSTOMER_SESSION_COOKIE)) redirect("/entrar?next=%2Fcarrinho");
  let cart: Cart;
  try {
    cart = await customerApi<Cart>("/cart");
  } catch (error) {
    if (error instanceof CustomerApiError && error.status === 401) redirect("/entrar?next=%2Fcarrinho");
    if (error instanceof CustomerApiError && error.status === 403) redirect("/acesso-pendente?next=%2Fcarrinho");
    throw error;
  }
  const { ok, erro } = await searchParams;
  const { quote, options } = cart;
  const currency = quote.currency;
  const chosen = cart.fulfillment ?? {};
  const current =
    chosen.type === "pickup"
      ? `pickup:${chosen.pickup_location_id ?? ""}`
      : chosen.type === "delivery"
        ? `delivery:${chosen.address_id ?? ""}`
        : "";
  const chosenSlot = chosen.slot_date && chosen.slot_start ? `${chosen.slot_date} ${chosen.slot_start}` : "";
  const slotChoices = chosen.type ? (options.slots[chosen.type] ?? []) : [];

  return (
    <StoreShell context={context}>
      <h1>Carrinho</h1>
      {ok === "adicionado" ? <p role="status">Item adicionado.</p> : null}
      {erro ? <p role="alert">{CART_ERRORS[erro] ?? "Não foi possível atualizar o carrinho."}</p> : null}
      {cart.items.length === 0 ? (
        <p>
          Seu carrinho está vazio. <Link href="/loja">Ver produtos</Link>
        </p>
      ) : (
        <>
          <table className={styles.lots}>
            <tbody>
              {cart.items.map((item) => (
                <tr key={item.id}>
                  <th scope="row">
                    {item.product_slug ? <Link href={`/loja/produto/${item.product_slug}`}>{item.name}</Link> : item.name}
                    {item.modifiers.length ? (
                      <div className="muted">{item.modifiers.map((m) => m.name).join(", ")}</div>
                    ) : null}
                    {item.problem ? (
                      <div className={styles.soldOut}>{PROBLEM[item.problem.code] ?? "Indisponível."}</div>
                    ) : null}
                  </th>
                  <td>{item.unit_price_cents !== null ? money(item.unit_price_cents, currency) : "—"}</td>
                  <td>
                    <form action={setCartQuantity} className={styles.buy}>
                      <input type="hidden" name="item_id" value={item.id} />
                      <input name="quantity" inputMode="decimal" defaultValue={item.quantity} size={4} aria-label="Quantidade" />
                      <button type="submit" className="muted">
                        Atualizar
                      </button>
                    </form>
                  </td>
                  <td>{item.subtotal_cents !== null ? money(item.subtotal_cents, currency) : "—"}</td>
                  <td>
                    <form action={removeCartItem}>
                      <input type="hidden" name="item_id" value={item.id} />
                      <button type="submit" className="muted">
                        Remover
                      </button>
                    </form>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>

          {quote.needs_fulfillment ? (
            <section className={styles.section}>
              <h2>Como receber</h2>
              {options.modes.length === 0 ? <p>A loja ainda não configurou retirada nem entrega.</p> : null}
              <form action={chooseFulfillment}>
                {options.modes.includes("pickup")
                  ? options.pickup_locations.map((loc) => (
                      <p key={loc.id}>
                        <label>
                          <input type="radio" name="choice" value={`pickup:${loc.id}`} defaultChecked={current === `pickup:${loc.id}`} />{" "}
                          Retirar em {loc.name} — {loc.address}
                        </label>
                      </p>
                    ))
                  : null}
                {options.modes.includes("delivery") ? (
                  options.addresses.length ? (
                    options.addresses.map((address) => (
                      <p key={address.id}>
                        <label>
                          <input
                            type="radio"
                            name="choice"
                            value={`delivery:${address.id}`}
                            defaultChecked={current === `delivery:${address.id}`}
                          />{" "}
                          Entregar em {address.label ? `${address.label}: ` : ""}
                          {address.summary}
                        </label>
                      </p>
                    ))
                  ) : (
                    <p>
                      Para entrega, <Link href="/conta/enderecos">cadastre um endereço</Link>.
                    </p>
                  )
                ) : null}
                {slotChoices.length ? (
                  <p>
                    <label>
                      Horário{" "}
                      <select name="slot" defaultValue={chosenSlot}>
                        <option value="">Escolha</option>
                        {slotChoices.map((slot) => (
                          <option key={`${slot.date} ${slot.start}`} value={`${slot.date} ${slot.start}`}>
                            {slotLabel(slot)}
                          </option>
                        ))}
                      </select>
                    </label>
                  </p>
                ) : null}
                {options.modes.length ? (
                  <button type="submit" className="button">
                    Usar esta opção
                  </button>
                ) : null}
              </form>
              {quote.fulfillment?.problems.length ? (
                <p role="alert">{quote.fulfillment.problems.map((p) => FULFILLMENT_PROBLEM[p] ?? p).join(" ")}</p>
              ) : null}
            </section>
          ) : null}

          <section className={styles.section}>
            <p>Subtotal: {money(quote.subtotal_cents, currency)}</p>
            {quote.discount_cents ? <p>Desconto: −{money(quote.discount_cents, currency)}</p> : null}
            {quote.delivery_fee_cents ? <p>Entrega: {money(quote.delivery_fee_cents, currency)}</p> : null}
            <p>
              <strong>Total: {money(quote.total_cents, currency)}</strong>
            </p>
            {quote.can_checkout ? (
              <Link href="/checkout" className="button">
                Finalizar compra
              </Link>
            ) : (
              <p className="muted">
                {quote.problems ? "Resolva os itens marcados para continuar." : "Escolha como receber para continuar."}
              </p>
            )}
          </section>
        </>
      )}
    </StoreShell>
  );
}
