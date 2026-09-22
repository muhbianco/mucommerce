"use server";

import { api } from "@/lib/panel/api";

import { FormError, id, money, optional, run, tenantBase, text } from "../form-kit";

const STATUSES = new Set([
  "accepted",
  "in_production",
  "ready_for_pickup",
  "shipped",
  "delivered",
  "cancelled",
]);

function version(form: FormData): number | undefined {
  const raw = text(form, "version");
  if (!raw) return undefined;
  const value = Number(raw);
  if (!Number.isInteger(value) || value < 1) throw new FormError("id_invalido");
  return value;
}

/**
 * Move the order along, or cancel it. The version on the page goes with it: if someone else
 * moved the order meanwhile, the API refuses instead of applying the wrong step.
 */
export async function moveOrder(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const orderId = id(text(form, "order_id"));
  await run(`${page}/pedidos/${orderId}`, "pedido", async () => {
    const to = text(form, "to");
    if (!STATUSES.has(to)) throw new FormError("id_invalido");
    await api(`${path}/orders/${orderId}/transition`, {
      json: {
        to,
        reason: optional(form, "reason"),
        expected_version: version(form),
        restock: form.get("restock") !== "off",
      },
    });
  });
}

/** Give money back on a paid order (partly or in full). */
export async function requestRefund(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const orderId = id(text(form, "order_id"));
  await run(`${page}/pedidos/${orderId}`, "devolucao", async () => {
    const reason = text(form, "reason");
    if (reason.length < 3) throw new FormError("motivo_obrigatorio");
    await api(`${path}/orders/${orderId}/refunds`, {
      json: { amount_cents: money(form, "amount"), reason },
    });
  });
}

/** Approve, refuse, or record a refund made by hand in the provider's app. */
export async function decideRefund(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const orderId = id(text(form, "order_id"));
  const refundId = id(text(form, "refund_id"));
  const decision = text(form, "decision");
  await run(`${page}/pedidos/${orderId}`, `devolucao_${decision}`, async () => {
    if (decision === "approve") {
      await api(`${path}/refunds/${refundId}/approve`, { json: {} });
      return;
    }
    if (decision === "reject") {
      const reason = text(form, "reason");
      if (reason.length < 3) throw new FormError("motivo_obrigatorio");
      await api(`${path}/refunds/${refundId}/reject`, { json: { reason } });
      return;
    }
    if (decision === "complete") {
      const evidence = text(form, "evidence");
      if (evidence.length < 10) throw new FormError("evidencia_obrigatoria");
      await api(`${path}/refunds/${refundId}/complete`, { json: { evidence } });
      return;
    }
    throw new FormError("id_invalido");
  });
}
