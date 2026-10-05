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
        // Checkbox desmarcado não vai no form; só "on" (marcado) devolve os itens ao estoque.
        restock: to !== "cancelled" || form.get("restock") === "on",
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
    // Itens marcados mandam a devolução; sem nenhum, vale o valor digitado. **O valor dos
    // itens não sai daqui**: quanto uma linha rendeu depende do desconto do pedido, e quem
    // calcula é o servidor. A tela manda o que foi escolhido, não quanto isso vale.
    const lines = refundLines(form);
    await api(`${path}/orders/${orderId}/refunds`, {
      json: lines.length
        ? { lines, reason, restock: form.get("restock") === "on" }
        : { amount_cents: money(form, "amount"), reason },
    });
  });
}

/** Os itens marcados na tabela, com a quantidade de cada um. */
function refundLines(form: FormData): { line_no: number; quantity_milli: number }[] {
  const lines: { line_no: number; quantity_milli: number }[] = [];
  for (const marcado of form.getAll("refund_line").map(String)) {
    const lineNo = Number(marcado);
    if (!Number.isInteger(lineNo) || lineNo < 1) throw new FormError("id_invalido");
    const quantidade = Number(text(form, `refund_qty_${lineNo}`) || 0);
    if (!Number.isFinite(quantidade) || quantidade <= 0) throw new FormError("quantidade_invalida");
    lines.push({ line_no: lineNo, quantity_milli: Math.round(quantidade * 1000) });
  }
  return lines;
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

/**
 * Compra a etiqueta e marca o pedido como enviado.
 *
 * Gasta dinheiro da carteira da loja, então é um clique consciente — nada aqui dispara sozinho.
 * A API é idempotente por pedido: clicar duas vezes não compra duas etiquetas.
 */
export async function dispatchShipment(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const orderId = id(text(form, "order_id"));
  await run(`${page}/pedidos/${orderId}`, "despachado", async () => {
    // A tela pediu confirmação (o frete subiu mais de 10% desde a compra) e ela não veio.
    if (text(form, "cost_check") === "1" && form.get("confirm_cost") !== "on") {
      throw new FormError("confirmar_custo");
    }
    // Pedido sem CPF/CNPJ de quem recebe (feito antes de o checkout pedir): quem despacha informa.
    const documento = text(form, "recipient_document").slice(0, 20);
    await api(`${path}/orders/${orderId}/shipment`, {
      method: "POST",
      json: documento ? { recipient_document: documento } : {},
    });
  });
}
