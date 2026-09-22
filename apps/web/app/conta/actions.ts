"use server";

import { redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";

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
