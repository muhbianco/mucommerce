"use server";

import { ApiError, api } from "@/lib/panel/api";
import { PAYMENT_PROVIDERS, type PaymentProviderSpec } from "@/lib/panel/types";

import { FormError, money, run, tenantBase, text } from "../form-kit";

const PROVIDER = /^[a-z]{2,24}$/;

function provider(form: FormData): { name: string; spec: PaymentProviderSpec } {
  const name = text(form, "provider");
  const spec = PROVIDER.test(name) ? PAYMENT_PROVIDERS[name] : undefined;
  if (!spec) throw new FormError("id_invalido");
  return { name, spec };
}

/**
 * The store's own credentials for one provider (owner only; the API refuses anyone else).
 * Secret fields left blank keep what is stored: secrets are never sent back to the page.
 */
export async function savePaymentProvider(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/pagamentos`, "pagamento", async () => {
    const { name, spec } = provider(form);
    const publicConfig: Record<string, string> = {};
    for (const { key } of spec.public) {
      const value = text(form, `public_${key}`);
      if (value) publicConfig[key] = value.slice(0, 200);
    }
    const credentials: Record<string, string> = {};
    for (const { key } of spec.secrets) {
      const value = text(form, `secret_${key}`);
      if (value) credentials[key] = value.slice(0, 500);
    }
    const methods = spec.methods.filter((m) => form.get(`method_${m}`) === "on");
    const installments = Number(text(form, "installments_max") || 1);
    try {
      await api(`${path}/payments/providers/${name}`, {
        method: "PUT",
        json: {
          enabled: form.get("enabled") === "on",
          is_default: form.get("is_default") === "on",
          sandbox: form.get("sandbox") === "on",
          public_config: publicConfig,
          methods: methods.length ? methods : null,
          installments_max: Number.isInteger(installments) ? Math.min(Math.max(installments, 1), 12) : 1,
          credentials,
        },
      });
    } catch (error) {
      if (error instanceof ApiError && Array.isArray(error.details.missing)) throw new FormError("pagamento_incompleto");
      throw error;
    }
  });
}

/** Ask the provider whether the stored credentials work (result shown on the page). */
export async function testPaymentProvider(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/pagamentos`, "pagamento_testado", async () => {
    await api(`${path}/payments/providers/${provider(form).name}/test`, { method: "POST", json: {} });
  });
}

/**
 * Repasse da taxa ao cliente (Lei 13.455/2017: pode, desde que informado — por isso o valor
 * aparece no checkout antes de a pessoa escolher o meio).
 */
export async function saveSurcharge(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/pagamentos`, "taxa", async () => {
    const bps = (name: string): number => {
      const raw = text(form, name).replace(",", ".");
      if (!raw) return 0;
      const percent = Number(raw);
      if (!Number.isFinite(percent) || percent < 0 || percent > 30) throw new FormError("percentual_invalido");
      return Math.round(percent * 100);
    };
    const tiers = [1, 6, 12]
      .map((upTo) => ({ up_to: upTo, percent_bps: bps(`card_${upTo}`), fixed_cents: 0 }))
      .filter((tier) => tier.percent_bps > 0);
    await api(`${path}/settings/payments`, {
      method: "PUT",
      json: {
        value: {
          enabled: form.get("surcharge_enabled") === "on",
          surcharge: {
            card: { percent_bps: bps("card_percent"), fixed_cents: money(form, "card_fixed") ?? 0 },
            pix: { percent_bps: bps("pix_percent"), fixed_cents: money(form, "pix_fixed") ?? 0 },
          },
          card_installments: tiers,
        },
      },
    });
  });
}
