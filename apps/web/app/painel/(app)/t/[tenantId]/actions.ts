"use server";

import { randomUUID } from "node:crypto";

import { redirect } from "next/navigation";

import { api, ApiError } from "@/lib/panel/api";
import { localToUtcIso, parseMoney, parseQuantity } from "@/lib/panel/format";
import type { Media, Product, TenantPanelContext, UploadCreated } from "@/lib/panel/types";

// Same contract as app/painel/actions.ts: Server Actions only accept same-origin POSTs (CSRF),
// the API authorises every call (membership + scope + flag), and outcomes come back as
// ?ok=/?erro= so pages work without client JavaScript. Ids from forms are shape-checked
// before they are placed in an API path.

const ID = /^[0-9a-f-]{36}$/;

class FormError extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function optional(form: FormData, name: string): string | null {
  return text(form, name) || null;
}

function id(value: string): string {
  if (!ID.test(value)) throw new FormError("id_invalido");
  return value;
}

function tenantBase(form: FormData): { path: string; page: string } {
  const tenantId = id(text(form, "tenant_id"));
  return { path: `/admin/tenants/${tenantId}`, page: `/t/${tenantId}` };
}

function money(form: FormData, name: string, { required = false } = {}): number | null {
  const cents = parseMoney(text(form, name));
  if (cents === null && required) throw new FormError("preco_obrigatorio");
  if (Number.isNaN(cents)) throw new FormError("preco_invalido");
  return cents;
}

function outcome(error: unknown): string {
  if (error instanceof FormError || error instanceof ApiError) return `erro=${error.code}`;
  throw error;
}

async function run(back: string, ok: string, work: () => Promise<string | void>): Promise<never> {
  let target = `${back}${back.includes("?") ? "&" : "?"}ok=${ok}`;
  try {
    const next = await work();
    if (next) target = next;
  } catch (error) {
    target = `${back}${back.includes("?") ? "&" : "?"}${outcome(error)}`;
  }
  redirect(target);
}

// ------------------------------------------------------------------ products
export async function createProduct(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/produtos`, "criado", async () => {
    const product = await api<Product>(`${path}/products`, {
      json: {
        name: text(form, "name"),
        base_price_cents: money(form, "price", { required: true }),
        ...(optional(form, "sku") ? { sku: text(form, "sku") } : {}),
      },
      idempotencyKey: optional(form, "idempotency_key") ?? undefined,
    });
    return `${page}/produtos/${product.id}?ok=criado`;
  });
}

export async function updateProduct(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const productId = id(text(form, "product_id"));
  const timeZone = text(form, "time_zone") || "America/Sao_Paulo";
  await run(`${page}/produtos/${productId}`, "salvo", async () => {
    const promoStarts = localToUtcIso(text(form, "promo_starts_at"), timeZone);
    const promoEnds = localToUtcIso(text(form, "promo_ends_at"), timeZone);
    if (promoStarts === "invalid" || promoEnds === "invalid") throw new FormError("data_invalida");
    await api(`${path}/products/${productId}`, {
      method: "PATCH",
      json: {
        name: text(form, "name"),
        slug: text(form, "slug"),
        short_description: optional(form, "short_description"),
        description_md: optional(form, "description_md"),
        base_price_cents: money(form, "price", { required: true }),
        promo_price_cents: money(form, "promo_price"),
        promo_starts_at: promoStarts,
        promo_ends_at: promoEnds,
        cost_cents_estimate: money(form, "cost"),
        stock_policy: text(form, "stock_policy"),
        sold_by: text(form, "sold_by"),
        unit_label: text(form, "unit_label") || "un",
        position: Number(text(form, "position") || 0),
        category_ids: form.getAll("category_ids").map(String).map(id),
        seo: { title: optional(form, "seo_title"), description: optional(form, "seo_description") },
      },
    });
  });
}

export async function setProductStatus(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const productId = id(text(form, "product_id"));
  const action = text(form, "action");
  await run(`${page}/produtos/${productId}`, action, async () => {
    if (action === "archive") {
      await api(`${path}/products/${productId}`, { method: "DELETE" });
      return `${page}/produtos?ok=arquivado`;
    }
    if (action !== "publish" && action !== "unpublish") throw new FormError("acao_invalida");
    await api(`${path}/products/${productId}/${action}`, { method: "POST" });
  });
}

export async function updateVariant(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const productId = id(text(form, "product_id"));
  const variantId = id(text(form, "variant_id"));
  await run(`${page}/produtos/${productId}`, "variante", async () => {
    await api(`${path}/products/${productId}/variants/${variantId}`, {
      method: "PATCH",
      json: { price_cents: money(form, "price"), cost_cents: money(form, "cost") },
    });
  });
}

// ------------------------------------------------------------------ media
/** Called from the image uploader (client component): returns the direct-upload form. */
export async function requestUpload(input: {
  tenantId: string;
  ownerType: "product" | "tenant_brand" | "landing";
  ownerId: string | null;
  mime: string;
  bytes: number;
  filename: string;
}): Promise<{ ok: true; mediaId: string; url: string; fields: Record<string, string> } | { ok: false; code: string }> {
  try {
    const created = await api<UploadCreated>(`/admin/tenants/${id(input.tenantId)}/media/uploads`, {
      json: {
        owner_type: input.ownerType,
        owner_id: input.ownerId ? id(input.ownerId) : null,
        mime: input.mime,
        bytes: input.bytes,
        filename: input.filename.slice(0, 200),
      },
    });
    return { ok: true, mediaId: created.media.id, url: created.upload.url, fields: created.upload.fields };
  } catch (error) {
    if (error instanceof ApiError || error instanceof FormError) return { ok: false, code: error.code };
    throw error;
  }
}

export async function completeUpload(tenantId: string, mediaId: string): Promise<Media["status"] | "error"> {
  try {
    const media = await api<Media>(`/admin/tenants/${id(tenantId)}/media/${id(mediaId)}/complete`, {
      method: "POST",
    });
    return media.status;
  } catch (error) {
    if (error instanceof ApiError || error instanceof FormError) return "error";
    throw error;
  }
}

export async function mediaStatus(
  tenantId: string,
  mediaId: string,
): Promise<{ status: Media["status"] | "error"; reason: string | null }> {
  try {
    const media = await api<Media>(`/admin/tenants/${id(tenantId)}/media/${id(mediaId)}`);
    return { status: media.status, reason: media.failure_reason };
  } catch (error) {
    if (error instanceof ApiError || error instanceof FormError) return { status: "error", reason: null };
    throw error;
  }
}

export async function deleteMedia(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const back = text(form, "back") === "config" ? `${page}/configuracoes` : `${page}/produtos/${id(text(form, "product_id"))}`;
  await run(back, "imagem_removida", async () => {
    await api(`${path}/media/${id(text(form, "media_id"))}`, { method: "DELETE" });
  });
}

export async function updateMediaAlt(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const productId = id(text(form, "product_id"));
  await run(`${page}/produtos/${productId}`, "imagem", async () => {
    await api(`${path}/media/${id(text(form, "media_id"))}`, {
      method: "PATCH",
      json: { alt: optional(form, "alt"), position: Number(text(form, "position") || 0) },
    });
  });
}

// ------------------------------------------------------------------ categories
export async function saveCategory(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const categoryId = optional(form, "category_id");
  const parentId = optional(form, "parent_id");
  const body = {
    name: text(form, "name"),
    parent_id: parentId ? id(parentId) : null,
    position: Number(text(form, "position") || 0),
    ...(optional(form, "slug") ? { slug: text(form, "slug") } : {}),
  };
  await run(`${page}/categorias`, categoryId ? "salva" : "criada", async () => {
    if (categoryId) {
      await api(`${path}/categories/${id(categoryId)}`, { method: "PATCH", json: body });
    } else {
      await api(`${path}/categories`, {
        json: body,
        idempotencyKey: optional(form, "idempotency_key") ?? undefined,
      });
    }
  });
}

export async function archiveCategory(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/categorias`, "arquivada", async () => {
    await api(`${path}/categories/${id(text(form, "category_id"))}`, { method: "DELETE" });
  });
}

// ------------------------------------------------------------------ stock
export async function adjustStock(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const variantId = id(text(form, "variant_id"));
  const back = text(form, "back") === "extrato" ? `${page}/estoque/${variantId}` : `${page}/estoque`;
  await run(back, "estoque", async () => {
    const quantity = parseQuantity(text(form, "quantity"));
    if (quantity === null) throw new FormError("quantidade_invalida");
    const kind = text(form, "kind");
    await api(`${path}/inventory/adjustments`, {
      json: {
        kind,
        reason: optional(form, "reason"),
        lines: [
          {
            variant_id: variantId,
            quantity,
            ...(kind === "receipt" && money(form, "unit_cost") !== null
              ? { unit_cost_cents: money(form, "unit_cost") }
              : {}),
          },
        ],
      },
      // Rendered once per form: resubmitting the same form replays instead of moving stock twice.
      idempotencyKey: text(form, "idempotency_key") || randomUUID(),
    });
  });
}

export async function setMinLevel(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const variantId = id(text(form, "variant_id"));
  await run(`${page}/estoque/${variantId}`, "minimo", async () => {
    const raw = text(form, "min_level");
    const minLevel = raw ? parseQuantity(raw) : null;
    if (raw && minLevel === null) throw new FormError("quantidade_invalida");
    await api(`${path}/inventory/variants/${variantId}/min-level`, {
      method: "PUT",
      json: { min_level: minLevel },
    });
  });
}

// ------------------------------------------------------------------ settings
async function currentSetting(path: string, key: string): Promise<Record<string, unknown>> {
  const context = await api<TenantPanelContext>(`${path}/context`);
  return context.settings[key] ?? {};
}

export async function saveBranding(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/configuracoes`, "marca", async () => {
    const current = await currentSetting(path, "branding");
    const logo = optional(form, "logo_media_id");
    await api(`${path}/settings/branding`, {
      method: "PUT",
      json: {
        value: {
          ...current,
          primary_color: text(form, "primary_color") || "#111111",
          logo_media_id: logo ? id(logo) : null,
        },
      },
    });
  });
}

export async function saveSeo(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/configuracoes`, "seo", async () => {
    const current = await currentSetting(path, "seo");
    const og = optional(form, "og_image_media_id");
    await api(`${path}/settings/seo`, {
      method: "PUT",
      json: {
        value: {
          ...current,
          title: optional(form, "title"),
          description: optional(form, "description"),
          og_image_media_id: og ? id(og) : null,
          indexable: form.get("indexable") === "on",
        },
      },
    });
  });
}

type Block = Record<string, unknown> & { type: string };

const EDITABLE_BLOCKS = ["hero", "featured_products", "text", "contact"];

/** The landing editor posts every block as `b<i>.<field>`; rebuild the list in order. */
function blocksFromForm(form: FormData): Block[] {
  const count = Number(text(form, "block_count") || 0);
  const blocks: Block[] = [];
  for (let i = 0; i < count; i += 1) {
    const get = (name: string) => optional(form, `b${i}.${name}`);
    const type = get("type");
    if (!type || form.get(`b${i}.remove`) === "on") continue;
    const block: Block = { type };
    for (const name of ["title", "subtitle", "cta_label", "body", "whatsapp_e164", "instagram", "email", "address", "hours"]) {
      const value = get(name);
      if (value !== null) block[name] = value;
    }
    const media = get("media_id");
    if (media) block.media_id = id(media);
    const products = form.getAll(`b${i}.product_ids`).map(String).map(id);
    if (type === "featured_products") block.product_ids = products;
    if (type === "hero") block.cta_target = get("cta_target") ?? "catalog";
    blocks.push(block);
  }
  return blocks;
}

export async function saveLanding(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/configuracoes`, "landing", async () => {
    const blocks = blocksFromForm(form);
    // Block types this editor does not show (made through the API) are kept, not dropped.
    const current = await currentSetting(path, "landing");
    const kept = ((current.blocks as Block[] | undefined) ?? []).filter(
      (block) => !EDITABLE_BLOCKS.includes(block.type),
    );
    blocks.push(...kept);
    const add = optional(form, "add_block");
    if (add === "contact") blocks.push({ type: "contact" });
    if (add === "hero") blocks.push({ type: "hero", title: "Bem-vindo" });
    if (add === "text") blocks.push({ type: "text", title: "Sobre nós", body: "" });
    if (add === "featured_products") {
      const productIds = form.getAll("add_product_ids").map(String).map(id);
      if (productIds.length === 0) throw new FormError("escolha_produtos");
      blocks.push({ type: "featured_products", title: "Destaques", product_ids: productIds });
    }
    await api(`${path}/settings/landing`, { method: "PUT", json: { value: { blocks } } });
  });
}

// ------------------------------------------------------------------ customers (whitelist)
const ACCESS_DECISIONS = new Set(["approved", "blocked", "revoked"]);

export async function setCustomerAccess(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const customerId = id(text(form, "customer_id"));
  const status = text(form, "status");
  const back = safeListPath(text(form, "back"), `${page}/clientes`);
  await run(back, `acesso_${status}`, async () => {
    if (!ACCESS_DECISIONS.has(status)) throw new FormError("acao_invalida");
    await api(`${path}/customers/${customerId}/access`, {
      json: { status, note: optional(form, "note") },
    });
  });
}

/** The list the decision came from (tab/search kept); never another tenant or host. */
function safeListPath(value: string, fallback: string): string {
  return value.startsWith(`${fallback}`) && !value.includes("//") ? value : fallback;
}
