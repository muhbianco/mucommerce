"use server";

import { api } from "@/lib/panel/api";

import { FormError, id, run, tenantBase, text } from "../../form-kit";

/**
 * As quatro decisões que a lojista toma sobre uma proposta.
 *
 * Nenhuma delas monta nada aqui: a API reserva a cota, enfileira e responde 202; quem chama o
 * modelo é o worker. Um `fetch` que esperasse a montagem prenderia o processo do Next por até
 * setenta e cinco segundos por lojista — e o primeiro timeout de gateway mataria a tela sem
 * matar a chamada, que continuaria custando.
 *
 * Publicar é `apply`, e do outro lado ele passa por `set_setting`: a mesma porta da edição à
 * mão, com validação, conferência de referências e auditoria.
 */

export async function requestDraft(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/vitrine/propostas`, "proposta_pedida", async () => {
    await api(`${path}/landing/drafts`, { method: "POST" });
  });
}

export async function applyDraft(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const draftId = id(text(form, "draft_id"));
  await run(`${page}/vitrine`, "proposta_publicada", async () => {
    await api(`${path}/landing/drafts/${draftId}/apply`, { method: "POST" });
  });
}

export async function discardDraft(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const draftId = id(text(form, "draft_id"));
  await run(`${page}/vitrine/propostas`, "proposta_descartada", async () => {
    await api(`${path}/landing/drafts/${draftId}/discard`, { method: "POST" });
  });
}

export async function refineDraft(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const draftId = id(text(form, "draft_id"));
  const instruction = text(form, "instruction");
  if (!instruction) throw new FormError("instrucao_vazia");
  await run(`${page}/vitrine/propostas`, "proposta_pedida", async () => {
    await api(`${path}/landing/drafts/${draftId}/refine`, {
      method: "POST",
      json: { instruction: instruction.slice(0, 200) },
    });
  });
}
