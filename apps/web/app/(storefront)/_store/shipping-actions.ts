"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import {
  CEP_COOKIE,
  CEP_FRESH_COOKIE,
  CEP_FRESH_MAX_AGE,
  CEP_MAX_AGE,
  customerCookie,
  safeStorePath,
} from "@/lib/customer-cookies";
import { cepDigits } from "@/lib/store/shipping";

const ID = /^[0-9a-f-]{36}$/;
const QUANTITY = /^\d{1,3}(?:[.,]\d{1,3})?$/;

function field(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

/**
 * "Calcular" do frete por CEP (produto e carrinho). Não cota nada: guarda o CEP no cookie e
 * volta para a página, que cota ao renderizar — um F5 recota em vez de mostrar preço velho.
 *
 * O CEP fica no cookie, nunca na URL (dado pessoal: histórico, log de acesso, link
 * compartilhado). Variante e quantidade vão na URL: não identificam ninguém, e o link
 * compartilhado mostra o mesmo cálculo.
 */
export async function estimateShipping(form: FormData): Promise<void> {
  const back = safeStorePath(field(form, "back") || "/loja").split(/[?#]/)[0] || "/loja";
  const cep = cepDigits(field(form, "cep"));
  if (!cep) redirect(`${back}?frete_erro=cep#frete`);
  const jar = await cookies();
  jar.set(CEP_COOKIE, cep, customerCookie(CEP_MAX_AGE));
  jar.set(CEP_FRESH_COOKIE, "1", customerCookie(CEP_FRESH_MAX_AGE));
  const params = new URLSearchParams();
  const variantId = field(form, "variant_id");
  if (ID.test(variantId)) params.set("variante", variantId);
  const quantity = field(form, "quantity").replace(",", ".");
  if (QUANTITY.test(quantity) && Number(quantity) > 0 && quantity !== "1") params.set("quantidade", quantity);
  const query = params.toString();
  redirect(`${back}${query ? `?${query}` : ""}#frete`);
}
