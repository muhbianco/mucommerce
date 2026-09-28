"use server";

import { redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { safeStorePath } from "@/lib/customer-cookies";

/** "Solicitar acesso": the store sees the request in its panel (Clientes). */
export async function requestAccess(form: FormData): Promise<void> {
  const next = safeStorePath(String(form.get("next") ?? "/loja"));
  const message = String(form.get("message") ?? "").trim().slice(0, 500) || null;
  let status = "pedido";
  try {
    await customerApi("/me/access/request", { json: { message } });
  } catch (error) {
    if (!(error instanceof CustomerApiError)) throw error;
    if (error.status === 401) redirect(`/entrar?next=${encodeURIComponent(next)}`);
    status = error.code === "already_approved" ? "liberado" : error.status === 429 ? "limite" : "erro";
  }
  if (status === "liberado") redirect(next);
  redirect(`/acesso-pendente?next=${encodeURIComponent(next)}&ok=${status}`);
}
