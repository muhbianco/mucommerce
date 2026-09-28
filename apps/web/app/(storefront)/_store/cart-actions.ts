"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { CART_COUNT_COOKIE, CART_COUNT_MAX_AGE, cartCountOf } from "@/lib/cart-count";
import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { customerCookie, safeStorePath } from "@/lib/customer-cookies";

// Cart writes from the storefront. Server Actions are same-origin only (CSRF); the API prices
// everything and checks the store's rules, so these only pass ids and quantities along. A
// visitor who is not signed in goes to /entrar and comes back to where they were.

const ID = /^[0-9a-f-]{36}$/;
const QUANTITY = /^\d{1,3}(?:[.,]\d{1,3})?$/;

function field(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function quantity(form: FormData): string {
  const raw = field(form, "quantity") || "1";
  if (!QUANTITY.test(raw)) return "invalid";
  return raw.replace(",", ".");
}

/**
 * Guarda quantas linhas o carrinho tem, para o ícone no cabeçalho.
 *
 * A resposta da escrita já traz o carrinho inteiro, então isto não custa nenhuma ida ao
 * servidor. Se o formato vier diferente do esperado, o cookie não é tocado: um número velho
 * incomoda menos do que um número errado, e ele vence sozinho em uma hora.
 */
async function rememberCartCount(cart: unknown): Promise<void> {
  if (!cart || typeof cart !== "object" || !Array.isArray((cart as { items?: unknown }).items)) return;
  (await cookies()).set(
    CART_COUNT_COOKIE,
    String(cartCountOf(cart as { items: unknown[] })),
    customerCookie(CART_COUNT_MAX_AGE),
  );
}

async function attempt(back: string, done: string, work: () => Promise<unknown>): Promise<never> {
  try {
    await rememberCartCount(await work());
  } catch (error) {
    if (!(error instanceof CustomerApiError)) throw error;
    if (error.status === 401) redirect(`/entrar?next=${encodeURIComponent(back)}`);
    if (error.status === 403) redirect(`/acesso-pendente?next=${encodeURIComponent(back)}`);
    redirect(`${back}${back.includes("?") ? "&" : "?"}erro=${encodeURIComponent(error.code)}`);
  }
  redirect(done);
}

export async function addToCart(form: FormData): Promise<void> {
  const back = safeStorePath(field(form, "back") || "/loja");
  const variantId = field(form, "variant_id");
  const modifierIds = form.getAll("modifier_ids").map(String).filter((value) => ID.test(value));
  const amount = quantity(form);
  if (!ID.test(variantId) || amount === "invalid") redirect(`${back}?erro=invalid_quantity`);
  await attempt(back, "/carrinho?ok=adicionado", () =>
    customerApi("/cart/items", { json: { variant_id: variantId, quantity: amount, modifier_ids: modifierIds } }),
  );
}

/**
 * Muda a quantidade de um item.
 *
 * Os botões "+" e "−" mandam `delta`: o `name`/`value` do botão que enviou o formulário entra
 * no FormData, então o passo a passo funciona com o JavaScript desligado — que é a régua de
 * todo o fluxo de compra aqui. O campo de texto continua existindo para quem digita,
 * necessário em produto vendido por peso.
 */
export async function setCartQuantity(form: FormData): Promise<void> {
  const itemId = field(form, "item_id");
  const delta = field(form, "delta");
  let amount = quantity(form);
  if (delta === "1" || delta === "-1") {
    const current = Number(field(form, "current").replace(",", "."));
    if (!Number.isFinite(current)) redirect("/carrinho?erro=invalid_quantity");
    // Zero remove o item: é o que "−" na última unidade quer dizer.
    const next = Math.max(0, current + Number(delta));
    amount = Number.isInteger(next) ? String(next) : next.toFixed(3);
  }
  if (!ID.test(itemId) || amount === "invalid") redirect("/carrinho?erro=invalid_quantity");
  if (amount === "0") {
    await attempt("/carrinho", "/carrinho", () => customerApi(`/cart/items/${itemId}`, { method: "DELETE" }));
  }
  await attempt("/carrinho", "/carrinho", () =>
    customerApi(`/cart/items/${itemId}`, { method: "PATCH", json: { quantity: amount } }),
  );
}

export async function removeCartItem(form: FormData): Promise<void> {
  const itemId = field(form, "item_id");
  if (!ID.test(itemId)) redirect("/carrinho");
  await attempt("/carrinho", "/carrinho", () => customerApi(`/cart/items/${itemId}`, { method: "DELETE" }));
}

/** "pickup:<location id>" or "delivery:<address id>", plus an optional "YYYY-MM-DD HH:MM" slot. */
export async function chooseFulfillment(form: FormData): Promise<void> {
  const [type, ref = ""] = field(form, "choice").split(":");
  const [slotDate, slotStart] = field(form, "slot").split(" ");
  const body: Record<string, string> = {};
  if (type === "pickup") {
    body.type = "pickup";
    body.pickup_location_id = ref.slice(0, 36);
  } else if (type === "delivery" && ID.test(ref)) {
    body.type = "delivery";
    body.address_id = ref;
  } else {
    redirect("/carrinho?erro=fulfillment_invalid");
  }
  if (slotDate && slotStart) {
    body.slot_date = slotDate;
    body.slot_start = slotStart;
  }
  await attempt("/carrinho", "/carrinho", () => customerApi("/cart/fulfillment", { method: "PUT", json: body }));
}

/**
 * Pede a cotação para um endereço. Não grava nada: só leva o endereço escolhido para a URL,
 * e a página cota a partir dela. Assim um F5 recota em vez de mostrar preço velho.
 */
export async function quoteShipping(form: FormData): Promise<void> {
  const addressId = field(form, "address_id");
  if (!ID.test(addressId)) redirect("/carrinho?erro=address_required");
  redirect(`/carrinho?frete=${addressId}`);
}

/**
 * Grava a cotação escolhida como forma de receber.
 *
 * O preço vem do navegador junto com a assinatura que nós emitimos. Não conferimos nada aqui:
 * quem confere é o servidor, no `evaluate` e de novo no `place` — adulterar o valor neste
 * formulário só produz `quote_invalid`.
 */
export async function chooseShipping(form: FormData): Promise<void> {
  const addressId = field(form, "address_id");
  if (!ID.test(addressId)) redirect("/carrinho?erro=address_required");
  let shipping: unknown;
  try {
    shipping = JSON.parse(field(form, "option"));
  } catch {
    redirect(`/carrinho?frete=${addressId}&erro=quote_required`);
  }
  if (!shipping || typeof shipping !== "object") redirect(`/carrinho?frete=${addressId}&erro=quote_required`);
  await attempt("/carrinho", "/carrinho?ok=frete", () =>
    customerApi("/cart/fulfillment", {
      method: "PUT",
      json: { type: "shipping", address_id: addressId, shipping },
    }),
  );
}

/** Use a coupon on the cart. A code that cannot be used comes back with the reason. */
export async function applyCoupon(form: FormData): Promise<void> {
  const code = field(form, "code").toUpperCase().slice(0, 40);
  if (!/^[A-Z0-9_-]{3,40}$/.test(code)) redirect("/carrinho?erro=coupon_invalid");
  await attempt("/carrinho", "/carrinho?ok=cupom", () =>
    customerApi("/cart/coupon", { method: "PUT", json: { code } }),
  );
}

export async function removeCoupon(): Promise<void> {
  await attempt("/carrinho", "/carrinho", () => customerApi("/cart/coupon", { method: "DELETE" }));
}
