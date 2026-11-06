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
  // Volta para as propostas, e não para o editor: é lá que fica o "restaurar o que estava".
  await run(`${page}/vitrine/propostas`, "proposta_publicada", async () => {
    await api(`${path}/landing/drafts/${draftId}/apply`, { method: "POST" });
  });
}

/** Devolve ao ar o que estava antes da última proposta publicada (marca, desenho e página). */
export async function revertDraft(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const draftId = id(text(form, "draft_id"));
  await run(`${page}/vitrine/propostas`, "proposta_restaurada", async () => {
    await api(`${path}/landing/drafts/${draftId}/revert`, { method: "POST" });
  });
}

/** Esquece a leitura de cada arquivo: a próxima proposta olha todo o material de novo. */
export async function resetBrandReading(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/vitrine/propostas`, "leitura_reiniciada", async () => {
    await api(`${path}/landing/brand/reset`, { method: "POST" });
  });
}

const ROLES = new Set(["banner", "place_photo", "product_photo", "team_photo", "logo", "texture", "other"]);

/** Corrige o que a imagem é. Imagem sem papel não vira mais foto de destaque (docs/16). */
export async function setMediaRole(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const mediaId = id(text(form, "media_id"));
  const role = text(form, "role");
  if (!ROLES.has(role)) throw new FormError("papel_invalido");
  await run(`${page}/vitrine/propostas`, "papel_salvo", async () => {
    await api(`${path}/media/${mediaId}`, { method: "PATCH", json: { role } });
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
