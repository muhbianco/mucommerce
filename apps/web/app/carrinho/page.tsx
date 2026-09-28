import type { Metadata } from "next";
import Link from "next/link";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";
import { formatPrice, type StorePrice } from "@/lib/storefront";

import { CART_ERRORS } from "../_store/add-to-cart";
import {
  applyCoupon,
  chooseFulfillment,
  chooseShipping,
  quoteShipping,
  removeCartItem,
  removeCoupon,
  setCartQuantity,
} from "../_store/cart-actions";
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

/** Uma cotação como a loja a recebeu, assinada por nós. Volta inteira no `place`. */
interface ShippingOption {
  provider: string;
  service_code: string;
  service_name: string;
  carrier: string;
  price_cents: number;
  delivery_days: number | null;
  quoted_at: string;
  signature: string;
  cart: string;
}

interface ShippingQuote {
  options: ShippingOption[];
  problem: string | null;
}

interface Cart {
  id: string | null;
  version: number;
  items: CartItem[];
  fulfillment: {
    type?: string;
    pickup_location_id?: string;
    address_id?: string;
    slot_date?: string;
    slot_start?: string;
    shipping?: { carrier?: string; service_name?: string; price_cents?: number; delivery_days?: number | null };
  } | null;
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
    coupon: { code: string; discount_cents: number; problem: string | null } | null;
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

const COUPON_PROBLEM: Record<string, string> = {
  coupon_not_found: "Esse cupom não existe nesta loja.",
  coupon_inactive: "Esse cupom não está valendo agora.",
  coupon_not_started: "Esse cupom ainda não começou a valer.",
  coupon_expired: "Esse cupom já passou da validade.",
  coupon_min_subtotal: "O pedido ainda não chegou ao valor mínimo do cupom.",
  coupon_exhausted: "Esse cupom acabou.",
  coupon_customer_limit: "Você já usou esse cupom o número de vezes permitido.",
};

/** Por que não há frete agora. Cada um manda o cliente para uma saída diferente. */
const SHIPPING_PROBLEM: Record<string, string> = {
  shipping_disabled: "Esta loja não está enviando por transportadora agora.",
  not_configured: "A loja ainda não terminou de configurar o envio.",
  missing_dimensions: "Falta o peso ou o tamanho de um item para calcular o frete. Avise a loja.",
  unavailable: "A transportadora não respondeu agora. Tente de novo em instantes.",
  no_service: "Nenhuma transportadora atende esse endereço com o que está no carrinho.",
  address_required: "Escolha um endereço para calcular.",
};

const FULFILLMENT_PROBLEM: Record<string, string> = {
  mode_unavailable: "Essa forma de receber não está disponível.",
  quote_required: "Calcule o frete e escolha uma opção.",
  quote_expired: "O frete que você escolheu venceu ou o carrinho mudou. Calcule de novo.",
  quote_invalid: "Não conseguimos confirmar esse frete. Calcule de novo.",
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

/** Prazo da transportadora, em dias úteis, ou nada quando ela não informou. */
function prazo(days: number | null): string {
  if (days === null) return "";
  return days === 1 ? " · 1 dia útil" : ` · até ${days} dias úteis`;
}

function slotLabel(slot: Slot): string {
  const day = new Date(`${slot.date}T12:00:00`).toLocaleDateString("pt-BR", { weekday: "short", day: "2-digit", month: "2-digit" });
  return `${day} ${slot.start}–${slot.end}`;
}

export default async function CartPage({
  searchParams,
}: {
  searchParams: Promise<{ ok?: string; erro?: string; frete?: string }>;
}) {
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
  const { ok, erro, frete } = await searchParams;
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

  // Frete é cotado na hora, nunca guardado entre visitas: preço de transportadora vence.
  // Só cota quando o cliente pediu (`?frete=<endereço>`), para não gastar chamada em quem
  // vai retirar no balcão.
  const shippingOn = options.modes.includes("shipping");
  let shippingQuote: ShippingQuote | null = null;
  if (shippingOn && frete) {
    try {
      shippingQuote = await customerApi<ShippingQuote>("/cart/shipping/options", {
        json: { address_id: frete },
      });
    } catch (error) {
      if (!(error instanceof CustomerApiError)) throw error;
      shippingQuote = { options: [], problem: error.code };
    }
  }

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
                {options.modes.includes("pickup") || options.modes.includes("delivery") ? (
                  <button type="submit" className="button">
                    Usar esta opção
                  </button>
                ) : null}
              </form>

              {shippingOn ? (
                <div>
                  <h3>Receber por transportadora</h3>
                  {chosen.type === "shipping" && chosen.shipping ? (
                    <p role="status">
                      Escolhido: {chosen.shipping.carrier} {chosen.shipping.service_name}
                      {typeof chosen.shipping.price_cents === "number"
                        ? ` — ${money(chosen.shipping.price_cents, currency)}`
                        : ""}
                      {prazo(chosen.shipping.delivery_days ?? null)}
                    </p>
                  ) : null}
                  {options.addresses.length === 0 ? (
                    <p>
                      Para calcular o frete, <Link href="/conta/enderecos">cadastre um endereço</Link>.
                    </p>
                  ) : (
                    <>
                      <form action={quoteShipping}>
                        {options.addresses.map((address) => (
                          <p key={address.id}>
                            <label>
                              <input
                                type="radio"
                                name="address_id"
                                value={address.id}
                                defaultChecked={(frete ?? chosen.address_id) === address.id}
                              />{" "}
                              {address.label ? `${address.label}: ` : ""}
                              {address.summary}
                            </label>
                          </p>
                        ))}
                        <button type="submit" className="button">
                          Calcular frete
                        </button>
                      </form>
                      {shippingQuote === null ? null : shippingQuote.problem ? (
                        <p role="alert">
                          {SHIPPING_PROBLEM[shippingQuote.problem] ??
                            "Não foi possível calcular o frete agora."}
                        </p>
                      ) : shippingQuote.options.length === 0 ? (
                        <p role="alert">{SHIPPING_PROBLEM.no_service}</p>
                      ) : (
                        <form action={chooseShipping}>
                          <input type="hidden" name="address_id" value={frete} />
                          {shippingQuote.options.map((option) => (
                            <p key={`${option.provider}:${option.service_code}`}>
                              <label>
                                <input
                                  type="radio"
                                  name="option"
                                  value={JSON.stringify(option)}
                                  required
                                />{" "}
                                {option.carrier} {option.service_name} —{" "}
                                {money(option.price_cents, currency)}
                                {prazo(option.delivery_days)}
                              </label>
                            </p>
                          ))}
                          <button type="submit" className="button">
                            Usar este frete
                          </button>
                        </form>
                      )}
                    </>
                  )}
                </div>
              ) : null}
              {quote.fulfillment?.problems.length ? (
                <p role="alert">{quote.fulfillment.problems.map((p) => FULFILLMENT_PROBLEM[p] ?? p).join(" ")}</p>
              ) : null}
            </section>
          ) : null}

          <section className={styles.section}>
            <p>Subtotal: {money(quote.subtotal_cents, currency)}</p>
            {quote.coupon && !quote.coupon.problem ? (
              <p>
                Cupom {quote.coupon.code} aplicado.{" "}
                <form action={removeCoupon} style={{ display: "inline" }}>
                  <button type="submit" className="muted">
                    tirar
                  </button>
                </form>
              </p>
            ) : (
              <form action={applyCoupon}>
                <label>
                  Cupom de desconto
                  <input name="code" maxLength={40} placeholder="ex.: BEMVINDO" />
                </label>
                <button type="submit">Aplicar cupom</button>
              </form>
            )}
            {quote.coupon?.problem ? (
              <p role="alert">{COUPON_PROBLEM[quote.coupon.problem] ?? "Cupom indisponível."}</p>
            ) : null}
            {quote.discount_cents ? <p>Desconto: −{money(quote.discount_cents, currency)}</p> : null}
            {quote.delivery_fee_cents ? (
              <p>
                {chosen.type === "shipping" ? "Frete" : "Entrega"}:{" "}
                {money(quote.delivery_fee_cents, currency)}
              </p>
            ) : null}
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
