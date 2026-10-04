import type { Metadata } from "next";
import Link from "next/link";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";
import { formatPrice, type StorePrice } from "@/lib/storefront";
import { freeShippingBase } from "@/lib/store/pricing";

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
import { EmptyState, FreeShippingBar, Notice, PageHead, Section, Split, TableWrap, Thumb } from "../_store/ui";

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
  /** O que cada transportadora respondeu ao recusar — texto delas. */
  refusals?: string[];
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

/** Cadastrar endereço sem perder a compra: o `next` traz a pessoa de volta ao carrinho. */
const NOVO_ENDERECO = "/conta/enderecos?next=%2Fcarrinho";

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
  /**
   * Quanto o preço cheio caiu, somando desconto progressivo e promoção.
   *
   * O cupom **não** entra aqui: ele abate o total depois, tem linha própria, e somar os dois
   * mostraria a mesma economia duas vezes. O `compare_at` é o riscado que a própria linha já
   * carrega, então isto não custa nenhuma ida à API.
   */
  const economia = cart.items.reduce((total, item) => {
    const cheio = item.compare_at_cents;
    const pago = item.unit_price_cents;
    if (!cheio || !pago || cheio <= pago) return total;
    return total + (cheio - pago) * Number(String(item.quantity).replace(",", "."));
  }, 0);
  const chosen = cart.fulfillment ?? {};
  const current =
    chosen.type === "pickup"
      ? `pickup:${chosen.pickup_location_id ?? ""}`
      : chosen.type === "delivery"
        ? `delivery:${chosen.address_id ?? ""}`
        : "";
  const chosenSlot = chosen.slot_date && chosen.slot_start ? `${chosen.slot_date} ${chosen.slot_start}` : "";
  const slotChoices = chosen.type ? (options.slots[chosen.type] ?? []) : [];

  const freeAbove = (context.fulfillment.shipping?.free_above_cents ?? null) as number | null;

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
      shippingQuote = { options: [], problem: error.code, refusals: [] };
    }
  }

  return (
    <StoreShell context={context}>
      <PageHead title="Carrinho" />
      {ok === "adicionado" ? <Notice kind="ok">Item adicionado.</Notice> : null}
      {ok === "cupom" ? <Notice kind="ok">Cupom aplicado.</Notice> : null}
      {erro ? <Notice kind="error">{CART_ERRORS[erro] ?? "Não foi possível atualizar o carrinho."}</Notice> : null}
      {cart.items.length === 0 ? (
        <EmptyState
          title="Seu carrinho está vazio."
          action={
            <Link className="button" href="/loja">
              Ver produtos
            </Link>
          }
        >
          O que você escolher aparece aqui, com o total e as formas de receber.
        </EmptyState>
      ) : (
        <Split
          aside={
            <div className={styles.summary} aria-label="Resumo do pedido">
              <h2>Resumo</h2>
              {shippingOn ? (
                <FreeShippingBar
                  subtotalCents={freeShippingBase(quote.subtotal_cents, quote.discount_cents)}
                  freeAboveCents={freeAbove}
                  currency={currency}
                />
              ) : null}
              <dl className={styles.totals}>
                {economia ? (
                  // O preço cheio antes do abatimento. Sem esta linha, o desconto progressivo e a
                  // promoção sumiam da conta: eles baixam o preço unitário, então o subtotal já
                  // nasce descontado e a pessoa nunca vê o quanto ganhou.
                  <div>
                    <dt>Preço cheio</dt>
                    <dd className={styles.strikethrough}>
                      {money(quote.subtotal_cents + economia, currency)}
                    </dd>
                  </div>
                ) : null}
                <div>
                  <dt>Subtotal</dt>
                  <dd>{money(quote.subtotal_cents, currency)}</dd>
                </div>
                {economia ? (
                  <div>
                    <dt>Você economizou</dt>
                    <dd className={styles.discount}>−{money(economia, currency)}</dd>
                  </div>
                ) : null}
                {quote.discount_cents ? (
                  <div>
                    <dt>Cupom {quote.coupon?.code ?? ""}</dt>
                    <dd className={styles.discount}>
                      −{money(quote.discount_cents, currency)}
                    </dd>
                  </div>
                ) : null}
                {quote.delivery_fee_cents ? (
                  <div>
                    <dt>{chosen.type === "shipping" ? "Frete" : "Entrega"}</dt>
                    <dd>{money(quote.delivery_fee_cents, currency)}</dd>
                  </div>
                ) : null}
                <div className={styles.totalRow}>
                  <dt>Total</dt>
                  <dd>{money(quote.total_cents, currency)}</dd>
                </div>
              </dl>

              {quote.coupon && !quote.coupon.problem ? (
                <p className={styles.couponOn}>
                  Cupom {quote.coupon.code} aplicado.{" "}
                  <form action={removeCoupon} style={{ display: "inline" }}>
                    <button type="submit" className={styles.linkButton}>
                      tirar
                    </button>
                  </form>
                </p>
              ) : (
                <form action={applyCoupon} className={styles.couponForm}>
                  <label htmlFor="cupom">Cupom de desconto</label>
                  <div className={styles.couponRow}>
                    <input id="cupom" name="code" maxLength={40} placeholder="ex.: BEMVINDO" />
                    <button type="submit">Aplicar cupom</button>
                  </div>
                </form>
              )}
              {quote.coupon?.problem ? (
                <Notice kind="warn">{COUPON_PROBLEM[quote.coupon.problem] ?? "Cupom indisponível."}</Notice>
              ) : null}

              {quote.can_checkout ? (
                <Link href="/checkout" className={`button ${styles.checkoutCta}`}>
                  Finalizar compra
                </Link>
              ) : (
                <p className="muted">
                  {quote.problems ? "Resolva os itens marcados para continuar." : "Escolha como receber para continuar."}
                </p>
              )}
              <Link href="/loja" className={styles.keepShopping}>
                Continuar comprando
              </Link>
            </div>
          }
        >
          <TableWrap>
            <table className={styles.cartTable}>
              <tbody>
                {cart.items.map((item) => (
                  <tr key={item.id}>
                    <th scope="row">
                      <span className={styles.cartItem}>
                        <Thumb url={item.image_url} />
                        <span className={styles.cartItemText}>
                          {item.product_slug ? (
                            <Link href={`/loja/produto/${item.product_slug}`}>{item.name}</Link>
                          ) : (
                            item.name
                          )}
                          {item.modifiers.length ? (
                            <span className="muted">{item.modifiers.map((m) => m.name).join(", ")}</span>
                          ) : null}
                          {item.unit_price_cents !== null ? (
                            <span className={styles.cartUnit}>{money(item.unit_price_cents, currency)} cada</span>
                          ) : null}
                          {item.problem ? (
                            <span className={styles.soldOut}>{PROBLEM[item.problem.code] ?? "Indisponível."}</span>
                          ) : null}
                        </span>
                      </span>
                    </th>
                    <td>
                      {/* Os botões mandam `delta`; o campo continua aqui para quem digita — e
                          para quem compra por peso, onde "+1" não quer dizer nada. */}
                      <form action={setCartQuantity} className={styles.stepper}>
                        <input type="hidden" name="item_id" value={item.id} />
                        <input type="hidden" name="current" value={item.quantity} />
                        <button type="submit" name="delta" value="-1" aria-label="Diminuir a quantidade">
                          −
                        </button>
                        <input name="quantity" inputMode="decimal" defaultValue={item.quantity} aria-label="Quantidade" />
                        <button type="submit" name="delta" value="1" aria-label="Aumentar a quantidade">
                          +
                        </button>
                        <button type="submit" className={styles.stepperApply}>
                          Atualizar
                        </button>
                      </form>
                    </td>
                    <td className={styles.cartSubtotal}>
                      {item.subtotal_cents !== null ? money(item.subtotal_cents, currency) : "—"}
                    </td>
                    <td>
                      <form action={removeCartItem}>
                        <input type="hidden" name="item_id" value={item.id} />
                        <button type="submit" className={styles.linkButton}>
                          Remover
                        </button>
                      </form>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>

          {quote.needs_fulfillment ? (
            <Section title="Como receber" variant="card">
              {options.modes.length === 0 ? <p>A loja ainda não configurou retirada nem entrega.</p> : null}
              <form action={chooseFulfillment}>
                {options.modes.includes("pickup")
                  ? options.pickup_locations.map((loc) => (
                      <p key={loc.id} className={styles.choice}>
                        <label>
                          <input
                            type="radio"
                            name="choice"
                            value={`pickup:${loc.id}`}
                            defaultChecked={current === `pickup:${loc.id}`}
                          />{" "}
                          Retirar em {loc.name} — {loc.address}
                        </label>
                      </p>
                    ))
                  : null}
                {options.modes.includes("delivery") ? (
                  options.addresses.length ? (
                    options.addresses.map((address) => (
                      <p key={address.id} className={styles.choice}>
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
                      Para entrega, <Link href={NOVO_ENDERECO}>cadastre um endereço</Link>.
                    </p>
                  )
                ) : null}
                {options.modes.includes("delivery") && options.addresses.length ? (
                  // Também com endereço cadastrado: quem entrega no trabalho hoje e em casa
                  // amanhã não tinha por onde acrescentar sem sair da compra e se achar sozinha.
                  <p className={styles.choice}>
                    <Link href={NOVO_ENDERECO}>+ Entregar em outro endereço</Link>
                  </p>
                ) : null}
                {slotChoices.length ? (
                  <p className={styles.choice}>
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
                <div className={styles.shipping}>
                  <h3>Receber por transportadora</h3>
                  {chosen.type === "shipping" && chosen.shipping ? (
                    <Notice kind="ok" role="status">
                      Escolhido: {chosen.shipping.carrier} {chosen.shipping.service_name}
                      {typeof chosen.shipping.price_cents === "number"
                        ? ` — ${money(chosen.shipping.price_cents, currency)}`
                        : ""}
                      {prazo(chosen.shipping.delivery_days ?? null)}
                    </Notice>
                  ) : null}
                  {options.addresses.length === 0 ? (
                    <p>
                      Para calcular o frete, <Link href={NOVO_ENDERECO}>cadastre um endereço</Link>.
                    </p>
                  ) : (
                    <>
                      <form action={quoteShipping}>
                        {options.addresses.map((address) => (
                          <p key={address.id} className={styles.choice}>
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
                        <button type="submit">Calcular frete</button>
                      </form>
                      <p className={styles.choice}>
                        <Link href={NOVO_ENDERECO}>+ Enviar para outro endereço</Link>
                      </p>
                      {shippingQuote === null ? null : shippingQuote.problem ? (
                        <Notice kind="warn">
                          {SHIPPING_PROBLEM[shippingQuote.problem] ?? "Não foi possível calcular o frete agora."}
                          {/* O motivo é da transportadora, e costuma ser a resposta inteira:
                              "as dimensões excedem o limite" manda olhar o produto, não o CEP. */}
                          {shippingQuote.refusals?.length ? (
                            <ul>
                              {shippingQuote.refusals.map((motivo) => (
                                <li key={motivo}>{motivo}</li>
                              ))}
                            </ul>
                          ) : null}
                        </Notice>
                      ) : shippingQuote.options.length === 0 ? (
                        <Notice kind="warn">{SHIPPING_PROBLEM.no_service}</Notice>
                      ) : (
                        <form action={chooseShipping} className={styles.quotes}>
                          <input type="hidden" name="address_id" value={frete} />
                          {shippingQuote.options.map((option) => (
                            <label key={`${option.provider}:${option.service_code}`} className={styles.quote}>
                              <input type="radio" name="option" value={JSON.stringify(option)} required />
                              <span className={styles.quoteName}>
                                {option.carrier} {option.service_name}
                                <span className="muted">{prazo(option.delivery_days).replace(/^ · /, "")}</span>
                              </span>
                              <span className={styles.quotePrice}>{money(option.price_cents, currency)}</span>
                            </label>
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
                <Notice kind="warn">
                  {quote.fulfillment.problems.map((p) => FULFILLMENT_PROBLEM[p] ?? p).join(" ")}
                </Notice>
              ) : null}
            </Section>
          ) : null}
        </Split>
      )}
    </StoreShell>
  );
}
