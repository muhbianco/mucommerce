"use server";

import { api } from "@/lib/panel/api";

import { FormError, id, money, optional, run, tenantBase, text } from "../form-kit";

const CODE = /^[A-Za-z0-9_-]{3,40}$/;
const STATUSES = new Set(["active", "paused", "archived"]);

function count(form: FormData, name: string): number | null {
  const raw = text(form, name);
  if (!raw) return null;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1) throw new FormError("numero_invalido");
  return value;
}

function percent(form: FormData, name: string): number | null {
  const raw = text(form, name).replace(",", ".");
  if (!raw) return null;
  const value = Math.round(Number(raw) * 100); // 12,5% → 1250 basis points
  if (!Number.isFinite(value) || value < 1 || value > 10000) throw new FormError("percentual_invalido");
  return value;
}

/** A date the store typed (YYYY-MM-DD) as the start of that day in the store's timezone. */
function when(form: FormData, name: string, timezone: string): string | null {
  const raw = text(form, name);
  if (!raw) return null;
  if (!/^\d{4}-\d{2}-\d{2}$/.test(raw)) throw new FormError("data_invalida");
  const offset = new Intl.DateTimeFormat("en-US", { timeZone: timezone, timeZoneName: "longOffset" })
    .formatToParts(new Date(`${raw}T12:00:00Z`))
    .find((part) => part.type === "timeZoneName")?.value;
  const zone = (offset ?? "GMT").replace("GMT", "") || "+00:00";
  return `${raw}T00:00:00${zone}`;
}

function body(form: FormData, timezone: string): Record<string, unknown> {
  const kind = text(form, "kind") === "fixed" ? "fixed" : "percent";
  return {
    kind,
    percent_bps: kind === "percent" ? percent(form, "percent") : null,
    amount_cents: kind === "fixed" ? money(form, "amount", { required: true }) : null,
    min_subtotal_cents: money(form, "min_subtotal") ?? 0,
    max_discount_cents: kind === "percent" ? money(form, "max_discount") : null,
    starts_at: when(form, "starts_at", timezone),
    ends_at: when(form, "ends_at", timezone),
    max_redemptions: count(form, "max_redemptions"),
    per_customer_limit: count(form, "per_customer_limit"),
    note: optional(form, "note"),
  };
}

export async function createCoupon(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/cupons`, "cupom_criado", async () => {
    const code = text(form, "code").toUpperCase();
    if (!CODE.test(code)) throw new FormError("codigo_invalido");
    const timezone = text(form, "timezone") || "America/Sao_Paulo";
    await api(`${path}/coupons`, { json: { code, status: "active", ...body(form, timezone) } });
  });
}

/** Edit a coupon, or just pause / resume / archive it. */
export async function updateCoupon(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const couponId = id(text(form, "coupon_id"));
  await run(`${page}/cupons`, "cupom_salvo", async () => {
    const status = text(form, "status");
    if (text(form, "only_status")) {
      if (!STATUSES.has(status)) throw new FormError("id_invalido");
      await api(`${path}/coupons/${couponId}`, { method: "PATCH", json: { status } });
      return;
    }
    const timezone = text(form, "timezone") || "America/Sao_Paulo";
    const changes = body(form, timezone);
    if (STATUSES.has(status)) changes.status = status;
    delete changes.kind; // the kind of discount is fixed once the coupon exists
    await api(`${path}/coupons/${couponId}`, { method: "PATCH", json: changes });
  });
}
