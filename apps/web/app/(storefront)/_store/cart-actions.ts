"use server";

import { redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { safeStorePath } from "@/lib/customer-cookies";

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

async function attempt(back: string, done: string, work: () => Promise<unknown>): Promise<never> {
  try {
    await work();
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

export async function setCartQuantity(form: FormData): Promise<void> {
  const itemId = field(form, "item_id");
  const amount = quantity(form);
  if (!ID.test(itemId) || amount === "invalid") redirect("/carrinho?erro=invalid_quantity");
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
