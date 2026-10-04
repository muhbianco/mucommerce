"use server";

import { api } from "@/lib/panel/api";
import { parseCm, parseWeight } from "@/lib/panel/measure";
import type { PackageKind, ShippingPackage } from "@/lib/panel/packaging";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import { FormError, id, money, run, tenantBase, text } from "../form-kit";

/**
 * Embalagens do frete v2. A loja digita centímetros e quilos; a API guarda milímetros e gramas.
 * Tudo volta como `?ok=`/`?erro=`, para a tela funcionar sem JavaScript.
 */

const KINDS = new Set<PackageKind>(["box", "envelope", "tube", "bag"]);

function cm(form: FormData, name: string, { required = false } = {}): number | null {
  const mm = parseCm(text(form, name));
  if (mm === null) {
    if (required) throw new FormError("medida_invalida");
    return null;
  }
  if (Number.isNaN(mm) || mm < 10 || mm > 21_000) throw new FormError("medida_invalida");
  return mm;
}

function grams(form: FormData, name: string, unit: "g" | "kg", fallback: number): number {
  const value = parseWeight(text(form, name), unit);
  if (value === null) return fallback;
  if (Number.isNaN(value) || value < 0) throw new FormError("peso_invalido");
  return value;
}

function kindOf(form: FormData): PackageKind {
  const kind = text(form, "kind") as PackageKind;
  if (!KINDS.has(kind)) throw new FormError("tipo_invalido");
  return kind;
}

/** O formulário da embalagem → corpo da API. Tubo: largura = altura = diâmetro. */
function packageBody(form: FormData): Record<string, unknown> {
  const kind = kindOf(form);
  const comprimento = cm(form, "inner_length", { required: true });
  const diametro = kind === "tube" ? cm(form, "inner_diameter", { required: true }) : null;
  const fora = ["outer_length", "outer_width", "outer_height"].map((name) => cm(form, name));
  const body: Record<string, unknown> = {
    name: text(form, "name"),
    kind,
    inner_length_mm: comprimento,
    inner_width_mm: diametro ?? cm(form, "inner_width", { required: true }),
    inner_height_mm: diametro ?? cm(form, "inner_height", { required: true }),
    // Medida de fora vem inteira ou não vem: a API recusa meia medida, e vazio = derivada.
    outer_length_mm: fora[0],
    outer_width_mm: fora[1],
    outer_height_mm: fora[2],
    empty_weight_grams: grams(form, "empty_weight", "g", 0),
    max_weight_grams: grams(form, "max_weight", "kg", 30_000),
    material_cost_cents: money(form, "material_cost"),
    auto_select: text(form, "uso") !== "restricted",
  };
  if (!body.name) {
    const dims = [comprimento, body.inner_width_mm, body.inner_height_mm] as number[];
    body.name = `${kind === "box" ? "Caixa" : kind === "tube" ? "Tubo" : kind === "bag" ? "Saco" : "Envelope"} ${dims
      .map((mm) => Math.round(mm / 10))
      .join("x")}`;
  }
  return body;
}

export async function createPackage(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/embalagens${text(form, "first") ? "" : "/nova"}`, "embalagem_criada", async () => {
    const created = await api<ShippingPackage>(`${path}/shipping/packages`, { json: packageBody(form) });
    return `${page}/embalagens/${created.id}?ok=embalagem_criada`;
  });
}

export async function updatePackage(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const packageId = id(text(form, "package_id"));
  await run(`${page}/embalagens/${packageId}`, "embalagem_salva", async () => {
    const body = packageBody(form);
    body.active = form.get("active") === "on";
    await api(`${path}/shipping/packages/${packageId}`, { method: "PATCH", json: body });
  });
}

/** Vale para a lista e para o detalhe: `back` diz para onde voltar. */
function back(form: FormData, page: string): string {
  return text(form, "back") === "detalhe" ? `${page}/embalagens/${text(form, "package_id")}` : `${page}/embalagens`;
}

export async function makeDefault(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const packageId = id(text(form, "package_id"));
  await run(back(form, page), "embalagem_padrao", async () => {
    await api(`${path}/shipping/packages/${packageId}/make-default`, { method: "POST" });
  });
}

export async function setActive(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const packageId = id(text(form, "package_id"));
  const active = text(form, "active") === "1";
  await run(back(form, page), active ? "embalagem_reativada" : "embalagem_arquivada", async () => {
    await api(`${path}/shipping/packages/${packageId}`, { method: "PATCH", json: { active } });
  });
}

export async function deletePackage(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const packageId = id(text(form, "package_id"));
  await run(`${page}/embalagens`, "embalagem_apagada", async () => {
    await api(`${path}/shipping/packages/${packageId}`, { method: "DELETE" });
  });
}

/** Atalho do estado vazio: cadastra um tamanho comum de uma vez (a lojista ajusta depois). */
export async function addPreset(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/embalagens`, "embalagem_criada", async () => {
    const [comprimento, largura, altura] = text(form, "dims").split("x").map(Number);
    const kind = kindOf(form);
    const created = await api<ShippingPackage>(`${path}/shipping/packages`, {
      json: {
        name: text(form, "name"),
        kind,
        inner_length_mm: comprimento,
        inner_width_mm: largura,
        inner_height_mm: altura,
        empty_weight_grams: Number(text(form, "tare")) || 0,
      },
    });
    return `${page}/embalagens/${created.id}?ok=embalagem_criada`;
  });
}

function int(form: FormData, name: string, min: number, max: number, fallback: number): number {
  const raw = text(form, name);
  if (!raw) return fallback;
  const value = Number(raw.replace(",", "."));
  if (!Number.isFinite(value) || value < min || value > max) throw new FormError("numero_invalido");
  return Math.round(value);
}

/**
 * Regras de embalagem da loja. Moram em `fulfillment.shipping.packing`, junto com retirada,
 * zonas e envio: relê e mescla, para esta tela nunca apagar o que as outras configuraram.
 */
export async function savePackingRules(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/embalagens`, "regras_embalagem", async () => {
    const context = await loadTenantContext(text(form, "tenant_id"));
    const fulfillment = { ...((context.settings.fulfillment ?? {}) as Record<string, unknown>) };
    const shipping = (fulfillment.shipping ?? {}) as Record<string, unknown>;
    const folgaMm = parseCm(text(form, "padding")) ?? 0;
    if (Number.isNaN(folgaMm) || folgaMm > 50) throw new FormError("medida_invalida");
    const packing = {
      padding_mm: folgaMm,
      flexible_fill_percent: int(form, "flexible_fill", 50, 100, 85),
      max_candidates: int(form, "max_candidates", 1, 4, 3),
      max_parcels: int(form, "max_parcels", 1, 20, 10),
      declare_value: form.get("declare_value") === "on",
      charge_material: form.get("charge_material") === "on",
    };
    await api(`${path}/settings/fulfillment`, {
      method: "PUT",
      json: { value: { ...fulfillment, shipping: { ...shipping, packing } } },
    });
  });
}
