"use server";

import { ApiError, api } from "@/lib/panel/api";
import { PAYMENT_PROVIDERS, type PaymentProviderSpec } from "@/lib/panel/types";

import { FormError, run, tenantBase, text } from "../form-kit";

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
