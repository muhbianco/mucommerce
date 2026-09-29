"use server";

import { api } from "@/lib/panel/api";

import { FormError, contactFields, optional, run, tenantBase, text } from "../../form-kit";
import { isStep, type StepKey } from "./steps";

/**
 * Cada passo salva sozinho, e manda **só os campos dele**.
 *
 * É o que faz o passo 2 não apagar o passo 1: a API funde por chave, então o que não viaja não é
 * tocado. Mandar o brief inteiro a cada tela criaria a corrida clássica — duas abas abertas, a
 * segunda gravando por cima com o que tinha na mão quando abriu.
 *
 * Campo vazio vira `null` de propósito: é assim que a lojista desfaz uma resposta. "Não mexi
 * nisso" é a chave nem aparecer no corpo, e isso é decidido aqui, pela lista de campos do passo.
 */

/** Uma linha por item, sem as vazias. É o formato que menos erra em `<textarea>`. */
function lines(form: FormData, name: string, max: number): string[] {
  return text(form, name)
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean)
    .slice(0, max);
}

/** Separadas por vírgula (as pessoas escrevem assim) e também por linha. */
function terms(form: FormData, name: string, max: number): string[] {
  return text(form, name)
    .split(/[,\n]/)
    .map((term) => term.trim())
    .filter(Boolean)
    .slice(0, max);
}

function patchFor(step: StepKey, form: FormData): Record<string, unknown> {
  switch (step) {
    case "negocio":
      return {
        segment: text(form, "segment") || "outro",
        segment_other: optional(form, "segment_other"),
        sells: text(form, "sells"),
      };
    case "publico":
      return {
        audience: optional(form, "audience"),
        differentials: lines(form, "differentials", 5),
        voice: text(form, "voice") || "proximo",
      };
    case "onde":
      return {
        city: optional(form, "city"),
        state: optional(form, "state")?.toUpperCase() ?? null,
        neighborhood: optional(form, "neighborhood"),
        // Caixa desmarcada não chega no FormData, então a lista sempre viaja inteira: é o que
        // permite desmarcar a última.
        serves: form.getAll("serves").map(String).slice(0, 4),
        hours_note: optional(form, "hours_note"),
        ...contactFields(form),
      };
    case "jeito":
      return {
        keywords: terms(form, "keywords", 8),
        avoid: optional(form, "avoid"),
        references: lines(form, "references", 3),
        notes: optional(form, "notes"),
      };
  }
}

export async function saveBriefStep(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const step = text(form, "step");
  if (!isStep(step)) throw new FormError("not_found");
  // Onde a lojista vai depois de salvar: o passo seguinte, ou a tela que oferece gerar.
  const next = text(form, "next") || `${page}/vitrine/brief/${step}`;

  await run(`${page}/vitrine/brief/${step}`, "brief", async () => {
    await api(`${path}/landing/brief`, { method: "PATCH", json: patchFor(step, form) });
    return `${next}?ok=brief`;
  });
}
