"use server";

import { redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";

// Places the reviewed cart. The idempotency key is rendered with the page, so a double click or
// a retried POST returns the same order instead of a second one. Server Actions are same-origin
// only; the API checks price, stock, delivery, the total and the terms again.

const ID = /^[0-9a-f-]{36}$/;
const KEY = /^[0-9a-f-]{36}$/;

function field(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function positiveInt(value: string): number | null {
  return /^\d{1,9}$/.test(value) ? Number(value) : null;
}

// Where each refusal sends the customer: back to the cart when the cart itself must change.
const TO_CART = new Set(["cart_changed", "cart_problems", "cart_empty", "fulfillment_invalid", "cart_already_converted"]);

export async function placeOrder(form: FormData): Promise<void> {
  const cartId = field(form, "cart_id");
  const key = field(form, "idempotency_key");
  const version = positiveInt(field(form, "cart_version"));
  const total = positiveInt(field(form, "expected_total_cents"));
  if (!ID.test(cartId) || !KEY.test(key) || version === null || total === null) redirect("/carrinho");
  if (form.get("accept") !== "on" && (field(form, "terms_version") || field(form, "privacy_version"))) {
    redirect("/checkout?erro=consent_required");
  }
  const terms = positiveInt(field(form, "terms_version"));
  const privacy = positiveInt(field(form, "privacy_version"));
  let orderId: string;
  try {
    ({ id: orderId } = await customerApi<{ id: string }>("/checkout/orders", {
      json: {
        cart_id: cartId,
        cart_version: version,
        expected_total_cents: total,
        contact: { name: field(form, "name").slice(0, 120), phone: field(form, "phone").slice(0, 20) || null },
        notes: field(form, "notes").slice(0, 500) || null,
        consent: { terms_version: terms, privacy_version: privacy },
      },
      headers: { "Idempotency-Key": key },
      timeoutMs: 15000,
    }));
  } catch (error) {
    if (!(error instanceof CustomerApiError)) throw error;
    if (error.status === 401) redirect("/entrar?next=%2Fcheckout");
    if (error.status === 403) redirect("/acesso-pendente?next=%2Fcheckout");
    redirect(`${TO_CART.has(error.code) ? "/carrinho" : "/checkout"}?erro=${encodeURIComponent(error.code)}`);
  }
  redirect(`/conta/pedidos/${orderId}?ok=pedido`);
}
