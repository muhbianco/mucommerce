"use server";

import { api } from "@/lib/panel/api";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import { FormError, money, run, tenantBase, text } from "../form-kit";

/**
 * Envio por transportadora. A configuração mora dentro de `fulfillment`, junto com retirada e
 * entrega por zona, então toda gravação **relê e mescla**: esta tela não pode apagar as zonas
 * do lojista, e a tela de entrega não pode apagar o envio.
 */

function checked(form: FormData, name: string): boolean {
  return form.get(name) === "on";
}

function int(form: FormData, name: string, fallback: number): number {
  const raw = text(form, name);
  if (!raw) return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 0) throw new FormError("numero_invalido");
  return value;
}

function cep(form: FormData, name: string): string {
  const digits = text(form, name).replace(/\D/g, "");
  if (digits.length !== 8) throw new FormError("cep_origem_invalido");
  return digits;
}

async function current(tenantId: string): Promise<Record<string, unknown>> {
  const context = await loadTenantContext(tenantId);
  return { ...((context.settings.fulfillment ?? {}) as Record<string, unknown>) };
}

async function saveMerged(
  form: FormData,
  ok: string,
  change: (shipping: Record<string, unknown>) => Record<string, unknown>,
): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/envio`, ok, async () => {
    const fulfillment = await current(text(form, "tenant_id"));
    const shipping = (fulfillment.shipping ?? {}) as Record<string, unknown>;
    await api(`${path}/settings/fulfillment`, {
      method: "PUT",
      json: { value: { ...fulfillment, shipping: change(shipping) } },
    });
  });
}

/** Endereço de onde a mercadoria sai: a transportadora cota a partir dele. */
export async function saveOrigin(form: FormData): Promise<void> {
  await saveMerged(form, "envio_origem", (shipping) => ({
    ...shipping,
    origin: {
      name: text(form, "origin_name"),
      postal_code: cep(form, "origin_postal_code"),
      address: text(form, "origin_address"),
      number: text(form, "origin_number"),
      complement: text(form, "origin_complement") || null,
      district: text(form, "origin_district"),
      city: text(form, "origin_city"),
      state: text(form, "origin_state").toUpperCase(),
      document: text(form, "origin_document").replace(/\D/g, "") || null,
      phone: text(form, "origin_phone").replace(/\D/g, "") || null,
      email: text(form, "origin_email") || null,
    },
  }));
}

/** Caixa padrão do empacotamento e as regras de preço do frete. */
export async function saveRules(form: FormData): Promise<void> {
  await saveMerged(form, "envio_regras", (shipping) => ({
    ...shipping,
    enabled: checked(form, "enabled"),
    box: {
      width_mm: int(form, "box_width_mm", 200),
      height_mm: int(form, "box_height_mm", 150),
      depth_mm: int(form, "box_depth_mm", 100),
      max_weight_grams: int(form, "box_max_weight_grams", 30000),
      empty_weight_grams: int(form, "box_empty_weight_grams", 0),
    },
    markup_percent: int(form, "markup_percent", 0),
    markup_cents: money(form, "markup") ?? 0,
    free_above_cents: money(form, "free_above"),
    handling_days: int(form, "handling_days", 0),
  }));
}

/** Conecta a conta da loja na transportadora (só o dono; o token compra etiqueta). */
export async function connectAccount(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/envio`, "envio_conectado", async () => {
    const token = text(form, "access_token");
    if (token.length < 10) throw new FormError("token_invalido");
    await api(`${path}/shipping/credentials`, { method: "PUT", json: { access_token: token } });
  });
}

/** Pergunta à transportadora se a credencial vale (e quanto tem de saldo). */
export async function testAccount(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/envio`, "envio_testado", async () => {
    await api(`${path}/shipping/test`, { method: "POST", json: {} });
  });
}
