"use server";

import { api } from "@/lib/panel/api";
import { normalizeWhatsapp } from "@/lib/panel/contact";
import { lines, parseCepRanges } from "@/lib/panel/format";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import { FormError, money, run, tenantBase, text } from "../form-kit";

function checked(form: FormData, name: string): boolean {
  return form.get(name) === "on";
}

function rows(form: FormData, name: string, max: number): number {
  const count = Number(text(form, name) || 0);
  return Number.isInteger(count) && count >= 0 ? Math.min(count, max) : 0;
}

function int(form: FormData, name: string, fallback: number | null): number | null {
  const raw = text(form, name);
  if (!raw) return fallback;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 0) throw new FormError("numero_invalido");
  return value;
}

async function current(tenantId: string): Promise<Record<string, unknown>> {
  return { ...((await loadTenantContext(tenantId)).settings.fulfillment ?? {}) } as Record<string, unknown>;
}

/** Pickup locations, delivery zones and time windows (PUT: the whole fulfillment setting).
 * Rows left without a name are dropped; the API keeps ids by id or name (stable references). */
export async function saveFulfillment(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/entrega`, "entrega", async () => {
    const locations = [];
    for (let i = 0; i < rows(form, "loc_rows", 10); i++) {
      const name = text(form, `loc_name_${i}`);
      if (!name) continue;
      locations.push({
        ...(text(form, `loc_id_${i}`) ? { id: text(form, `loc_id_${i}`) } : {}),
        name,
        address: text(form, `loc_address_${i}`),
        instructions: text(form, `loc_instructions_${i}`) || null,
        active: checked(form, `loc_active_${i}`),
      });
    }
    const zones = [];
    for (let i = 0; i < rows(form, "zone_rows", 50); i++) {
      const name = text(form, `zone_name_${i}`);
      if (!name) continue;
      const kind = text(form, `zone_kind_${i}`) === "districts" ? "districts" : "cep_ranges";
      const cepRanges = parseCepRanges(text(form, `zone_ceps_${i}`));
      if (cepRanges === null) throw new FormError("cep_invalido");
      zones.push({
        ...(text(form, `zone_id_${i}`) ? { id: text(form, `zone_id_${i}`) } : {}),
        name,
        kind,
        cep_ranges: kind === "cep_ranges" ? cepRanges : [],
        city: text(form, `zone_city_${i}`) || null,
        state: text(form, `zone_state_${i}`).toUpperCase() || null,
        districts: kind === "districts" ? lines(text(form, `zone_districts_${i}`)) : [],
        fee_cents: money(form, `zone_fee_${i}`) ?? 0,
        min_order_cents: money(form, `zone_min_${i}`),
        eta_minutes: int(form, `zone_eta_${i}`, null),
        active: checked(form, `zone_active_${i}`),
        courier: text(form, `zone_courier_${i}`) === "uber" ? "uber" : "own",
      });
    }
    const windows = [];
    for (let i = 0; i < rows(form, "win_rows", 28); i++) {
      const start = text(form, `win_start_${i}`);
      const end = text(form, `win_end_${i}`);
      if (!start || !end) continue;
      const modes = (["pickup", "delivery"] as const).filter((mode) => checked(form, `win_${mode}_${i}`));
      windows.push({ weekday: int(form, `win_weekday_${i}`, 0), start, end, modes });
    }
    // `fulfillment` guarda retirada, zonas **e** envio por transportadora. Reler antes de
    // gravar é o que impede esta tela de apagar a configuração de envio (e vice-versa).
    // A coleta e a política da Uber também moram em `delivery`: esta parte do formulário não
    // as mostra, então elas voltam como estavam.
    const atual = (await current(text(form, "tenant_id"))) as { delivery?: Record<string, unknown> };
    await api(`${path}/settings/fulfillment`, {
      method: "PUT",
      json: {
        value: {
          ...atual,
          pickup: { enabled: checked(form, "pickup_enabled"), locations },
          delivery: { ...(atual.delivery ?? {}), enabled: checked(form, "delivery_enabled"), zones },
          min_order_cents: money(form, "min_order") ?? 0,
          scheduling: {
            enabled: checked(form, "scheduling_enabled"),
            windows,
            min_lead_minutes: int(form, "min_lead_minutes", 60),
            days_ahead: int(form, "days_ahead", 7),
          },
        },
      },
    });
  });
}

/** Order deadline, auto-accept, customer cancel window, open-order cap, refund threshold. */
export async function saveCheckout(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/entrega`, "checkout", async () => {
    await api(`${path}/settings/checkout`, {
      method: "PUT",
      json: {
        value: {
          reservation_mode: "reserve_on_place",
          pix_ttl_minutes: int(form, "pix_ttl_minutes", 30),
          auto_accept: checked(form, "auto_accept"),
          require_whatsapp: checked(form, "require_whatsapp"),
          customer_cancel_until: text(form, "customer_cancel_until") === "payment_confirmed" ? "payment_confirmed" : "accepted",
          max_open_orders: int(form, "max_open_orders", 3),
          refund_four_eyes_threshold_cents: money(form, "refund_threshold") ?? 0,
        },
      },
    });
  });
}

/**
 * De onde o entregador da Uber coleta, se ele é chamado sozinho no "Pronto" e o teto da corrida.
 * Mora em `fulfillment.delivery`, então relê e mescla (as zonas não podem sumir daqui).
 */
export async function saveUberSettings(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/entrega`, "uber_config", async () => {
    const name = text(form, "origin_name");
    let origin: Record<string, unknown> | null = null;
    if (name || text(form, "origin_address")) {
      const cep = text(form, "origin_cep").replace(/\D/g, "");
      if (cep.length !== 8) throw new FormError("cep_origem_invalido");
      const phone = normalizeWhatsapp(text(form, "origin_phone"));
      if (!phone) throw new FormError("telefone_coleta_invalido");
      const state = text(form, "origin_state").toUpperCase();
      if (!/^[A-Z]{2}$/.test(state)) throw new FormError("uf_invalida");
      origin = {
        name: name || "Loja",
        postal_code: cep,
        address: text(form, "origin_address"),
        number: text(form, "origin_number"),
        district: text(form, "origin_district"),
        city: text(form, "origin_city"),
        state,
        complement: text(form, "origin_complement") || null,
        phone,
        notes: text(form, "origin_notes").slice(0, 280) || null,
      };
    }
    const atual = (await current(text(form, "tenant_id"))) as { delivery?: Record<string, unknown> };
    const delivery = { ...(atual.delivery ?? {}) };
    const uber = { ...((delivery.uber as Record<string, unknown> | undefined) ?? {}) };
    await api(`${path}/settings/fulfillment`, {
      method: "PUT",
      json: {
        value: {
          ...atual,
          delivery: {
            ...delivery,
            origin,
            uber: {
              ...uber,
              auto_dispatch: checked(form, "uber_auto_dispatch"),
              max_fee_cents: money(form, "uber_max_fee"),
            },
          },
        },
      },
    });
  });
}

/** Conecta a conta Uber Direct da loja (só o dono). A API confere as três chaves na Uber antes. */
export async function connectUber(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/entrega`, "uber_conectada", async () => {
    const chaves = {
      customer_id: text(form, "uber_customer_id"),
      client_id: text(form, "uber_client_id"),
      client_secret: text(form, "uber_client_secret"),
    };
    if (Object.values(chaves).some((v) => v.length < 4 || v.length > 200)) throw new FormError("chave_uber_invalida");
    const environment = text(form, "uber_environment") === "test" ? "test" : "production";
    await api(`${path}/delivery/uber/credentials`, { method: "PUT", json: { ...chaves, environment } });
  });
}

/** Confere de novo as chaves guardadas na Uber (o resultado aparece na seção). */
export async function testUber(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/entrega`, "uber_testada", async () => {
    await api(`${path}/delivery/uber/test`, { method: "POST", json: {} });
  });
}

/** Apaga as chaves da Uber. Zonas da Uber continuam cobrando a taxa, mas ninguém é chamado. */
export async function disconnectUber(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/entrega`, "uber_desconectada", async () => {
    await api(`${path}/delivery/uber/credentials`, { method: "DELETE" });
  });
}
