"use server";

import { api } from "@/lib/panel/api";
import type { TenantPanelContext } from "@/lib/panel/types";

import { contactFields, id, optional, run, tenantBase, text } from "../form-kit";

/**
 * O editor da página inicial.
 *
 * Um formulário por bloco, e não um gigante: antes a página inteira ia junto a cada salvar, e
 * um campo inválido em qualquer bloco devolvia tudo. Aqui só o bloco mexido viaja.
 *
 * Todo bloco é endereçado pelo `id` que o servidor atribui, nunca pela posição na lista. Com
 * posição, subir o terceiro com outra aba aberta gravaria "o terceiro" querendo dizer outro
 * bloco — e a página sairia embaralhada sem ninguém entender por quê.
 *
 * Bloco de um tipo que este painel ainda não conhece (API mais nova, meio de um deploy) é
 * preservado intacto: some da tela de edição, não do site.
 */

type Block = Record<string, unknown> & { type: string; id?: string };

async function landingBlocks(path: string): Promise<Block[]> {
  const context = await api<TenantPanelContext>(`${path}/context`);
  const landing = context.settings.landing as { blocks?: Block[] } | undefined;
  return landing?.blocks ?? [];
}

async function saveBlocks(path: string, blocks: Block[]): Promise<void> {
  await api(`${path}/settings/landing`, { method: "PUT", json: { value: { blocks } } });
}

/** Onde o bloco está na lista, ou -1. */
function indexOf(blocks: Block[], blockId: string): number {
  return blocks.findIndex((b) => b.id === blockId);
}

// ------------------------------------------------------------------ ordem e existência

export async function moveBlock(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const blockId = id(text(form, "block_id"));
  const up = text(form, "dir") === "up";
  await run(`${page}/vitrine`, "ordem", async () => {
    const blocks = await landingBlocks(path);
    const at = indexOf(blocks, blockId);
    const to = up ? at - 1 : at + 1;
    // Já está na ponta: salvar seria escrever a mesma coisa e somar uma linha no audit log.
    if (at < 0 || to < 0 || to >= blocks.length) return;
    [blocks[at], blocks[to]] = [blocks[to]!, blocks[at]!];
    await saveBlocks(path, blocks);
  });
}

export async function duplicateBlock(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const blockId = id(text(form, "block_id"));
  await run(`${page}/vitrine`, "duplicado", async () => {
    const blocks = await landingBlocks(path);
    const at = indexOf(blocks, blockId);
    if (at < 0) return;
    // Manda o mesmo id duas vezes de propósito: o servidor dá um novo à cópia.
    blocks.splice(at + 1, 0, { ...blocks[at]! });
    await saveBlocks(path, blocks);
  });
}

export async function removeBlock(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const blockId = id(text(form, "block_id"));
  await run(`${page}/vitrine`, "removido", async () => {
    const blocks = await landingBlocks(path);
    await saveBlocks(
      path,
      blocks.filter((b) => b.id !== blockId),
    );
  });
}

export async function addBlock(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const type = text(form, "type");
  await run(`${page}/vitrine`, "adicionado", async () => {
    const blocks = await landingBlocks(path);
    const novo = NEW_BLOCK[type];
    if (!novo) return;
    await saveBlocks(path, [...blocks, novo()]);
  });
}

/**
 * Como cada tipo nasce.
 *
 * Com conteúdo de exemplo, não vazio: bloco em branco no meio da página deixa a loja no ar com
 * um buraco até alguém voltar para preencher. Texto de exemplo é feio, mas é visível — e o
 * lojista troca em vez de esquecer.
 */
const NEW_BLOCK: Record<string, () => Block> = {
  hero: () => ({ type: "hero", title: "Bem-vindo", cta_label: "Ver produtos" }),
  announcement: () => ({ type: "announcement", text: "Aviso da loja" }),
  featured_products: () => ({ type: "featured_products", title: "Destaques", product_ids: [] }),
  categories: () => ({ type: "categories", title: "Categorias", category_ids: [] }),
  benefits: () => ({
    type: "benefits",
    title: "Por que comprar aqui",
    items: [
      { icon: "store", title: "Primeiro motivo" },
      { icon: "clock", title: "Segundo motivo" },
    ],
  }),
  text: () => ({ type: "text", title: "Sobre a loja", body: "Conte a sua história." }),
  gallery: () => ({ type: "gallery", title: "Fotos", media_ids: [] }),
  testimonials: () => ({
    type: "testimonials",
    title: "Quem já comprou",
    items: [{ text: "O que um cliente disse.", author: "Nome" }],
  }),
  faq: () => ({
    type: "faq",
    title: "Perguntas frequentes",
    items: [
      { question: "Vocês entregam?", answer: "Responda aqui." },
      { question: "Como pago?", answer: "Responda aqui." },
    ],
  }),
  hours: () => ({ type: "hours", title: "Onde e quando", days: [] }),
  contact: () => ({ type: "contact", title: "Contato" }),
  cta: () => ({ type: "cta", title: "Bora?", cta_label: "Ver produtos" }),
};

// ------------------------------------------------------------------ conteúdo de um bloco

/** Lista curta vinda de textarea: uma por linha, vazias fora. */
function lines(form: FormData, name: string, max: number): string[] {
  return text(form, name)
    .split("\n")
    .map((linha) => linha.trim())
    .filter(Boolean)
    .slice(0, max);
}

/** "Pergunta = resposta", uma por linha. É o formato que cabe num textarea sem virar planilha. */
function pairs(form: FormData, name: string, max: number): [string, string][] {
  return lines(form, name, max)
    .map((linha) => {
      const at = linha.indexOf("=");
      return at < 0 ? null : ([linha.slice(0, at).trim(), linha.slice(at + 1).trim()] as [string, string]);
    })
    .filter((par): par is [string, string] => Boolean(par && par[0] && par[1]));
}

const CTA_TARGETS = ["catalog", "chat"];
const LINK_TARGETS = ["catalog", "chat", "none"];

function pick(form: FormData, name: string, allowed: string[], fallback: string): string {
  const value = text(form, name);
  return allowed.includes(value) ? value : fallback;
}

/**
 * Salva um bloco. O que a tela não mostra para aquele tipo fica como estava — é assim que um
 * campo que só a API conhece sobrevive a uma edição pelo painel.
 */
export async function saveBlock(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const blockId = id(text(form, "block_id"));
  await run(`${page}/vitrine`, "bloco", async () => {
    const blocks = await landingBlocks(path);
    const at = indexOf(blocks, blockId);
    if (at < 0) return;
    const atual = blocks[at]!;
    blocks[at] = { ...atual, ...fieldsFor(atual.type, form) };
    await saveBlocks(path, blocks);
  });
}

function fieldsFor(type: string, form: FormData): Record<string, unknown> {
  const comum: Record<string, unknown> = {};
  const variant = text(form, "variant");
  if (variant) comum.variant = variant;
  const tone = text(form, "tone");
  if (tone) comum.tone = tone;

  switch (type) {
    case "hero":
      return {
        ...comum,
        title: text(form, "title"),
        subtitle: optional(form, "subtitle"),
        media_id: optional(form, "media_id"),
        cta_label: optional(form, "cta_label"),
        cta_target: pick(form, "cta_target", CTA_TARGETS, "catalog"),
      };
    case "announcement":
      return {
        ...comum,
        text: text(form, "text"),
        link_target: pick(form, "link_target", LINK_TARGETS, "none"),
      };
    case "featured_products":
      return {
        ...comum,
        title: text(form, "title"),
        product_ids: form.getAll("product_ids").map(String).filter(Boolean).slice(0, 12),
      };
    case "categories":
      return {
        ...comum,
        title: text(form, "title"),
        category_ids: form.getAll("category_ids").map(String).filter(Boolean).slice(0, 12),
      };
    case "text":
      return {
        ...comum,
        title: optional(form, "title"),
        body: text(form, "body"),
        media_id: optional(form, "media_id"),
      };
    case "gallery":
      return {
        ...comum,
        title: optional(form, "title"),
        media_ids: form.getAll("media_ids").map(String).filter(Boolean).slice(0, 12),
      };
    case "benefits":
      return {
        ...comum,
        title: optional(form, "title"),
        items: form
          .getAll("benefit_icon")
          .map(String)
          .map((icon, i) => ({
            icon,
            title: String(form.getAll("benefit_title")[i] ?? "").trim(),
            text: String(form.getAll("benefit_text")[i] ?? "").trim() || null,
          }))
          .filter((item) => item.title)
          .slice(0, 6),
      };
    case "testimonials":
      return {
        ...comum,
        title: optional(form, "title"),
        items: pairs(form, "items", 6).map(([author, texto]) => ({ author, text: texto })),
      };
    case "faq":
      return {
        ...comum,
        title: optional(form, "title"),
        items: pairs(form, "items", 8).map(([question, answer]) => ({ question, answer })),
      };
    case "hours":
      return {
        ...comum,
        title: optional(form, "title"),
        address: optional(form, "address"),
        city: optional(form, "city"),
        state: optional(form, "state")?.toUpperCase() || null,
        note: optional(form, "note"),
        days: form
          .getAll("day_weekday")
          .map(String)
          .map((weekday, i) => ({
            weekday: Number(weekday),
            opens: String(form.getAll("day_opens")[i] ?? "").trim(),
            closes: String(form.getAll("day_closes")[i] ?? "").trim(),
          }))
          .filter((d) => d.opens && d.closes)
          .slice(0, 14),
      };
    case "cta":
      return {
        ...comum,
        title: text(form, "title"),
        subtitle: optional(form, "subtitle"),
        cta_label: text(form, "cta_label"),
        cta_target: pick(form, "cta_target", CTA_TARGETS, "catalog"),
      };
    case "contact":
      return {
        ...comum,
        title: text(form, "title") || "Contato",
        // O esquema exige E.164, perfil sem "@" e e-mail minúsculo; ninguém digita assim. Mandar
        // cru devolvia 422 com "algum campo está inválido" e sem dizer qual — ver
        // `lib/panel/contact`.
        ...contactFields(form),
        address: optional(form, "address"),
        hours: optional(form, "hours"),
      };
    default:
      // Tipo que este painel não conhece: nada a escrever, e o bloco fica como está.
      return {};
  }
}
