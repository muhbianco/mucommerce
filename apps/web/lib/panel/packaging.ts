/**
 * Embalagens do frete v2: tipos da API e os textos que a tela usa (docs/13-frete-v2.md §7).
 * Medidas em milímetros e gramas, como a API; a tela converte com `lib/panel/measure`.
 */

export type PackageKind = "box" | "envelope" | "tube" | "bag";

export interface ShippingPackage {
  id: string;
  name: string;
  kind: PackageKind;
  inner_length_mm: number;
  inner_width_mm: number;
  inner_height_mm: number;
  outer_length_mm: number | null;
  outer_width_mm: number | null;
  outer_height_mm: number | null;
  /** O que a transportadora cobra: a informada, ou a de dentro mais a parede. */
  billed_outer_mm: number[];
  empty_weight_grams: number;
  max_weight_grams: number;
  material_cost_cents: number | null;
  auto_select: boolean;
  is_default: boolean;
  active: boolean;
  position: number;
  rules_count: number;
}

export interface PackageUsage {
  product_id: string;
  name: string;
}

export interface PackagePreview {
  billed_outer_mm: number[];
  cubic_grams: number;
  cubic_free: boolean;
  warnings: string[];
  fits: { product_id: string; name: string; units: number }[];
}

export interface PackageCapacity {
  package_id: string;
  name: string;
  kind: PackageKind;
  is_default: boolean;
  allowed: boolean;
  calculated: number;
  declared: number | null;
  effective: number;
  declared_check: string | null;
  declared_percent: number | null;
  reason: string | null;
}

export interface SimParcel {
  package_id: string | null;
  package_name: string;
  kind: string;
  outer_mm: number[];
  gross_grams: number;
  billable_correios_grams: number;
  value_cents: number;
  material_cents: number;
  own: boolean;
  oversize: boolean;
  declared: boolean;
  items: { key: string; name: string; sku: string; units: number }[];
}

export interface SimQuote {
  service_code: string;
  service_name: string;
  carrier: string;
  /** O que o cliente pagaria (com o acréscimo da loja). */
  price_cents: number | null;
  delivery_min: number | null;
  delivery_max: number | null;
  error: string | null;
  mode: string | null;
  /** É a combinação que a vitrine ofereceria para este serviço. */
  best: boolean;
}

export interface SimPlan {
  strategy: string;
  hash: string;
  degraded: boolean;
  quoted: boolean;
  estimate_cents: Record<string, number | null>;
  parcels: SimParcel[];
  quotes?: SimQuote[];
}

export interface SimulateOut {
  plans: SimPlan[];
  problem: string | null;
  missing: string[];
  fallbacks: string[];
  quote_problem?: string | null;
}

export const QUOTE_PROBLEM_TEXT: Record<string, string> = {
  shipping_disabled: "Para cotar de verdade, ligue o envio por transportadora e informe a origem em Envio.",
  not_configured: "Para cotar de verdade, conecte a conta da transportadora em Envio.",
};

export interface PackingSettings {
  padding_mm: number;
  flexible_fill_percent: number;
  max_candidates: number;
  max_parcels: number;
  declare_value: boolean;
  charge_material: boolean;
}

export const PACKING_DEFAULTS: PackingSettings = {
  padding_mm: 0,
  flexible_fill_percent: 85,
  max_candidates: 3,
  max_parcels: 10,
  declare_value: true,
  charge_material: false,
};

/** As regras de embalagem da loja, com o padrão onde ela ainda não mexeu. */
export function packingSettings(settings: Record<string, Record<string, unknown>>): PackingSettings {
  const shipping = (settings.fulfillment?.shipping ?? {}) as { packing?: Partial<PackingSettings> };
  return { ...PACKING_DEFAULTS, ...(shipping.packing ?? {}) };
}

export const PACKAGE_KIND_LABEL: Record<PackageKind, string> = {
  box: "Caixa",
  envelope: "Envelope",
  tube: "Tubo",
  bag: "Saco",
};

/**
 * Tamanhos para começar. São pontos de partida editáveis, **não** medidas oficiais dos
 * Correios — não rotular nenhum como "padrão Correios" sem conferir.
 */
export interface PackagePreset {
  key: string;
  label: string;
  kind: PackageKind;
  inner: [number, number, number];
  tare: number;
}

/** A sugestão de partida do assistente da primeira vez. */
export const DEFAULT_PRESET: PackagePreset = {
  key: "m",
  label: "Caixa M",
  kind: "box",
  inner: [300, 200, 150],
  tare: 180,
};

export const PACKAGE_PRESETS: PackagePreset[] = [
  { key: "envelope", label: "Envelope", kind: "envelope", inner: [250, 180, 30], tare: 20 },
  { key: "p", label: "Caixa P", kind: "box", inner: [200, 150, 100], tare: 120 },
  DEFAULT_PRESET,
  { key: "g", label: "Caixa G", kind: "box", inner: [400, 300, 200], tare: 300 },
];

export const PREVIEW_WARNING: Record<string, string> = {
  correios_limits: "Passa do limite dos Correios (100 cm por lado, 200 cm somados): PAC e SEDEX somem da cotação.",
  nonmech_side:
    "Algum lado passa de 70 cm: os Correios tratam como não mecanizável e o frete sobe bastante (no teste, o PAC foi de R$ 64,74 para R$ 85,22 ao passar de 69 para 75 cm).",
  nonmech_shape:
    "Tubo é formato cilíndrico: a cotação mede como caixa, e os Correios podem cobrar a taxa de não mecanizável na postagem.",
  over_30kg: "Aguenta mais de 30 kg, mas os Correios só levam até 30 kg por volume.",
};

export const STRATEGY_LABEL: Record<string, string> = {
  consolidate: "Menos volumes",
  correios_fit: "Dentro do limite dos Correios",
  cubic_free: "Sem peso cúbico (até 30 L)",
  per_product: "Uma caixa por produto",
};

export const DECLARED_CHECK_TEXT: Record<string, string> = {
  ok: "",
  over_physical: "Declaração acima do espaço físico: confira se o produto amassa tanto assim.",
  too_compressed: "O produto teria de encolher para menos da metade: o sistema não aceita.",
  does_not_fit: "Nem uma unidade cabe nesta embalagem.",
  too_heavy: "Essa quantidade passa do peso que a embalagem aguenta.",
  missing_measures: "Informe peso e medidas para declarar quantos cabem.",
};

export const CAPACITY_REASON_TEXT: Record<string, string> = {
  does_not_fit: "não cabe",
  too_heavy: "pesado demais",
};
