"use server";

import { redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { safeCheckoutUrl } from "@/lib/payments";

const ID = /^[0-9a-f-]{36}$/;
const BACK = "/conta/enderecos";

function field(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim().slice(0, 300) : "";
}

async function attempt(ok: string, work: () => Promise<unknown>, back = BACK): Promise<never> {
  try {
    await work();
  } catch (error) {
    if (error instanceof CustomerApiError) {
      if (error.status === 401) redirect(`/entrar?next=${encodeURIComponent(back)}`);
      redirect(`${back}?erro=${encodeURIComponent(error.code)}`);
    }
    throw error;
  }
  redirect(`${back}?ok=${ok}`);
}

/** New address, or changes to one (`address_id`). Server Actions are same-origin only. */
export async function saveAddress(form: FormData): Promise<void> {
  const addressId = field(form, "address_id");
  if (addressId && !ID.test(addressId)) throw new Error("bad address id");
  const body = {
    label: field(form, "label") || null,
    recipient_name: field(form, "recipient_name"),
    phone: field(form, "phone") || null,
    postal_code: field(form, "postal_code"),
    street: field(form, "street"),
    number: field(form, "number"),
    complement: field(form, "complement") || null,
    district: field(form, "district"),
    city: field(form, "city"),
    state: field(form, "state"),
    reference: field(form, "reference") || null,
    is_default: form.get("is_default") === "on",
  };
  await attempt(addressId ? "salvo" : "criado", () =>
    addressId
      ? customerApi(`/me/addresses/${addressId}`, { method: "PATCH", json: body })
      : customerApi("/me/addresses", { json: body }),
  );
}

export async function deleteAddress(form: FormData): Promise<void> {
  const addressId = field(form, "address_id");
  if (!ID.test(addressId)) throw new Error("bad address id");
  await attempt("apagado", () => customerApi(`/me/addresses/${addressId}`, { method: "DELETE" }));
}

/** Cancel one of my orders (before payment, or paid within the store's window). */
export async function cancelOrder(form: FormData): Promise<void> {
  const orderId = field(form, "order_id");
  if (!ID.test(orderId)) throw new Error("bad order id");
  const back = `/conta/pedidos/${orderId}`;
  await attempt(
    "cancelado",
    () => customerApi(`/me/orders/${orderId}/cancel`, { json: { reason: field(form, "reason").slice(0, 200) || null } }),
    back,
  );
}

// ------------------------------------------------------------------------------- payments
const KEY = /^[0-9a-f-]{36}$/;
const PROVIDER = /^[a-z]{2,24}$/;
const METHODS = new Set(["pix", "link"]);

async function payment(orderId: string, ok: string, work: () => Promise<unknown>): Promise<never> {
  return attempt(ok, work, `/conta/pedidos/${orderId}`);
}

/**
 * Start paying an order. The idempotency key comes with the rendered page: a double click or a
 * retried POST gets the same payment, never a second charge. The provider may take a while.
 */
export async function startPayment(form: FormData): Promise<void> {
  const orderId = field(form, "order_id");
  const key = field(form, "idempotency_key");
  const provider = field(form, "provider");
  const method = field(form, "method");
  if (!ID.test(orderId) || !KEY.test(key) || !PROVIDER.test(provider) || !METHODS.has(method)) {
    throw new Error("bad payment request");
  }
  const back = `/conta/pedidos/${orderId}`;
  let created: { status: string; checkout_url: string | null };
  try {
    created = await customerApi(`/checkout/orders/${orderId}/payments`, {
      json: { provider, method },
      headers: { "Idempotency-Key": key },
      timeoutMs: 30000,
    });
  } catch (error) {
    if (!(error instanceof CustomerApiError)) throw error;
    if (error.status === 401) redirect(`/entrar?next=${encodeURIComponent(back)}`);
    redirect(`${back}?erro=${encodeURIComponent(error.code)}`);
  }
  // A payment link (redirect providers) goes straight to the provider's page.
  const link = created.status === "requires_action" ? safeCheckoutUrl(created.checkout_url) : null;
  redirect(link ?? `${back}?ok=pagamento`);
}

/** "I already paid": ask the provider now instead of waiting for its notice. */
export async function checkPayment(form: FormData): Promise<void> {
  const orderId = field(form, "order_id");
  const paymentId = field(form, "payment_id");
  if (!ID.test(orderId) || !ID.test(paymentId)) throw new Error("bad payment id");
  await payment(orderId, "conferido", () =>
    customerApi(`/checkout/payments/${paymentId}/check`, { json: {}, timeoutMs: 30000 }),
  );
}

/** Give up on this payment to pay another way (the order stays open). */
export async function cancelPayment(form: FormData): Promise<void> {
  const orderId = field(form, "order_id");
  const paymentId = field(form, "payment_id");
  if (!ID.test(orderId) || !ID.test(paymentId)) throw new Error("bad payment id");
  await payment(orderId, "trocar", () => customerApi(`/checkout/payments/${paymentId}/cancel`, { json: {} }));
}

const CARD_TOKEN = /^[A-Za-z0-9_-]{8,256}$/;
const CARD_METHOD = /^[a-z0-9_]{2,40}$/;
const ISSUER = /^[0-9]{1,40}$/;
const DOCUMENT = /^(?:[0-9]{11}|[0-9]{14})$/;

/**
 * Pay with a card tokenized in the browser by the provider's SDK (Mercado Pago Card Brick):
 * only the single-use token and its metadata come here. Called from the client component, so it
 * returns the query string for the order page instead of redirecting.
 */
export async function payWithCard(input: {
  orderId: string;
  idempotencyKey: string;
  token: string;
  paymentMethodId: string;
  issuerId: string | null;
  installments: number;
  identificationType: string | null;
  identificationNumber: string | null;
}): Promise<string> {
  const document = (input.identificationNumber ?? "").replace(/\D/g, "");
  const type = input.identificationType === "CNPJ" ? "CNPJ" : "CPF";
  if (
    !ID.test(input.orderId) ||
    !KEY.test(input.idempotencyKey) ||
    !CARD_TOKEN.test(input.token) ||
    !CARD_METHOD.test(input.paymentMethodId) ||
    (input.issuerId !== null && input.issuerId !== "" && !ISSUER.test(String(input.issuerId))) ||
    !Number.isInteger(input.installments) ||
    input.installments < 1 ||
    input.installments > 12
  ) {
    return "erro=validation_error";
  }
  const back = `/conta/pedidos/${input.orderId}`;
  const requestId = input.idempotencyKey;
  try {
    const created = await customerApi<{ status: string; failure_code: string | null }>(
      `/checkout/orders/${input.orderId}/payments`,
      {
        json: {
          provider: "mercadopago",
          method: "card",
          card: {
            token: input.token,
            payment_method_id: input.paymentMethodId,
            issuer_id: input.issuerId ? String(input.issuerId) : null,
            installments: input.installments,
          },
          payer_identification: DOCUMENT.test(document) ? { type, number: document } : null,
        },
        headers: { "Idempotency-Key": requestId },
        timeoutMs: 30000,
      },
    );
    if (created.status === "rejected") return `erro=${encodeURIComponent(created.failure_code ?? "provider_refused")}`;
    return "ok=pagamento";
  } catch (error) {
    if (!(error instanceof CustomerApiError)) throw error;
    if (error.status === 401) redirect(`/entrar?next=${encodeURIComponent(back)}`);
    return `erro=${encodeURIComponent(error.code)}`;
  }
}
