/** Shapes returned by api-commerce (v1) that the panel renders. */

export interface Membership {
  tenant_id: string;
  tenant_slug: string;
  role: string;
  scopes: string[];
}

export interface Me {
  id: string;
  email: string;
  full_name: string;
  platform_role: string | null;
  memberships: Membership[];
}

export interface TenantPanelContext {
  tenant_id: string;
  slug: string;
  name: string;
  status: string;
  timezone: string;
  currency: string;
  primary_host: string | null;
  features: Record<string, boolean>;
  settings: Record<string, Record<string, unknown>>;
}

export interface Tenant {
  id: string;
  slug: string;
  name: string;
  status: string;
  plan: string;
  timezone: string;
  created_at: string;
  activated_at: string | null;
}

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface Domain {
  id: string;
  hostname: string;
  kind: string;
  purpose: string;
  role: string;
  status: string;
  tls_status: string;
  last_error: string | null;
}

export interface TenantListItem extends Tenant {
  primary_host: string | null;
  owners: { admin_user_id: string; account_id: string | null; email: string; full_name: string; role: string }[];
}

// ------------------------------------------------------------------ catalog (phase 1)
export interface Price {
  amount_cents: number;
  compare_at_cents: number | null;
  promo_active: boolean;
  promo_ends_at: string | null;
}

export interface Rendition {
  name: string;
  url: string;
  width: number;
  height: number;
}

export interface Media {
  id: string;
  owner_type: string;
  owner_id: string | null;
  status: "pending" | "processing" | "ready" | "failed";
  alt: string | null;
  position: number;
  width: number | null;
  height: number | null;
  failure_reason: string | null;
  renditions: Rendition[];
  created_at: string;
}

export interface UploadCreated {
  media: Media;
  upload: { url: string; fields: Record<string, string>; expires_at: string };
}

export interface Variant {
  id: string;
  sku: string;
  name: string;
  price_cents: number | null;
  cost_cents: number | null;
  status: "active" | "paused" | "inactive";
  option_values: Record<string, string> | null;
  price: Price;
  paused_reason: string | null;
}

export interface ProductSummary {
  id: string;
  sku: string;
  slug: string;
  name: string;
  status: "draft" | "active" | "paused" | "inactive" | "archived";
  kind: string;
  base_price_cents: number;
  price: Price;
  position: number;
  published_at: string | null;
  updated_at: string;
  cover_url: string | null;
}

export interface Product extends ProductSummary {
  short_description: string | null;
  description_md: string | null;
  promo_price_cents: number | null;
  promo_starts_at: string | null;
  promo_ends_at: string | null;
  cost_cents_estimate: number | null;
  stock_policy: string;
  sold_by: string;
  unit_label: string;
  seo: { title?: string | null; description?: string | null } | null;
  category_ids: string[];
  tags: TagRef[];
  has_variants: boolean;
  options: { name: string; values: string[] }[];
  modifier_groups: ModifierGroup[];
  paused_at: string | null;
  paused_reason: string | null;
  variants: Variant[];
  media: Media[];
}

export interface ModifierGroup {
  id: string;
  name: string;
  min_select: number;
  max_select: number;
  modifiers: { id: string; name: string; price_cents: number; active: boolean }[];
}

/** Groups shown in the panel's modifiers form (the API allows up to 10). */
export const MODIFIER_GROUP_ROWS = 4;

/** Rows of the panel's options form (the API allows up to 3 options). */
export const PRODUCT_OPTION_ROWS = 3;

export interface TagRef {
  slug: string;
  name: string;
}

export interface Category {
  id: string;
  parent_id: string | null;
  slug: string;
  name: string;
  description: string | null;
  position: number;
}

export interface Balance {
  variant_id: string;
  sku: string;
  product_id: string;
  product_name: string;
  variant_name: string;
  unit_label: string;
  sold_by: string;
  on_hand: string;
  reserved: string;
  available: string;
  min_level: string | null;
  low_stock: boolean;
}

export interface Movement {
  id: string;
  movement_type: string;
  quantity: string;
  balance_after: string;
  unit_cost_micro: number | null;
  reason: string | null;
  actor: string;
  occurred_at: string;
}

export const PRODUCT_STATUS_LABEL: Record<string, string> = {
  draft: "rascunho",
  active: "publicado",
  paused: "pausado",
  inactive: "despublicado",
  archived: "arquivado",
};

export const PRODUCT_KINDS: Record<string, string> = {
  physical: "Produto",
  made_to_order: "Feito sob encomenda",
  service: "Serviço",
  digital: "Digital",
  ticket: "Ingresso de evento",
};

export interface EventLot {
  id: string;
  variant_id: string;
  sku: string;
  name: string;
  price_cents: number;
  quantity: number;
  available: number;
  sales_starts_at: string | null;
  sales_ends_at: string | null;
  position: number;
  state: "upcoming" | "on_sale" | "sold_out" | "ended" | "unavailable";
}

export interface ProductEvent {
  product_id: string;
  starts_at: string;
  ends_at: string | null;
  venue_name: string | null;
  venue_address: string | null;
  city: string | null;
  online_url: string | null;
  capacity: number | null;
  allocated: number;
  status: "scheduled" | "postponed" | "cancelled";
  status_note: string | null;
  lots: EventLot[];
}

export const EVENT_STATUS_LABEL: Record<ProductEvent["status"], string> = {
  scheduled: "Confirmado",
  postponed: "Adiado",
  cancelled: "Cancelado",
};

export const LOT_STATE_LABEL: Record<EventLot["state"], string> = {
  on_sale: "à venda",
  upcoming: "vendas em breve",
  sold_out: "esgotado",
  ended: "encerrado",
  unavailable: "fora de venda (produto não publicado, pausado ou evento não confirmado)",
};

export const STOCK_POLICIES: Record<string, string> = {
  tracked: "Controlar estoque",
  made_to_order: "Sob encomenda",
  unlimited: "Sempre disponível",
  untracked: "Não controlar",
};

export const MOVEMENT_LABEL: Record<string, string> = {
  purchase_in: "Entrada",
  loss: "Perda",
  adjustment: "Ajuste",
  count: "Contagem",
  initial: "Saldo inicial",
  sale_commit: "Venda",
  sale_return: "Devolução",
};
