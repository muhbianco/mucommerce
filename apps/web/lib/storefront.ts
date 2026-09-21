/** Storefront data shapes and pure helpers (no server-only imports: tested with vitest). */

import type { StorefrontContext } from "./tenant";

/** Shapes of api-commerce's /storefront endpoints (F1/S7). */
export interface Rendition {
  name: string;
  url: string;
  width: number;
  height: number;
}

export interface StoreImage {
  alt: string | null;
  width: number | null;
  height: number | null;
  renditions: Rendition[]; // smallest first
}

export interface StorePrice {
  amount_cents: number;
  compare_at_cents: number | null;
  promo_active: boolean;
  promo_ends_at: string | null;
  currency: string;
}

export type Availability = "available" | "sold_out" | "made_to_order";

export interface ProductCard {
  id: string;
  slug: string;
  name: string;
  short_description: string | null;
  price: StorePrice;
  availability: Availability;
  image: StoreImage | null;
}

export interface CategoryRef {
  id: string;
  parent_id: string | null;
  slug: string;
  name: string;
  description: string | null;
}

export interface ProductDetail extends ProductCard {
  sku: string;
  description_md: string | null;
  unit_label: string;
  sold_by: string;
  variants: { id: string; sku: string; name: string; price: StorePrice; availability: Availability }[];
  images: StoreImage[];
  categories: CategoryRef[];
  seo: { title: string | null; description: string | null };
  updated_at: string;
}

export interface ProductPage {
  items: ProductCard[];
  next_cursor: string | null;
}

export type LandingBlock = Record<string, unknown> & { type: string };

/** A store may be indexed only when it is live, public and the tenant opted in. */
export function isIndexable(context: StorefrontContext): boolean {
  return (
    context.tenant.status === "active" &&
    context.access_mode === "public" &&
    (context.seo as { indexable?: boolean }).indexable === true
  );
}

export function storeOrigin(context: StorefrontContext): string {
  return `https://${context.primary_host ?? context.host ?? ""}`;
}

export function formatPrice(price: StorePrice): string {
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency: price.currency }).format(
    price.amount_cents / 100,
  );
}

/** `srcset` from renditions (smallest first). */
export function srcSet(image: StoreImage): string {
  return image.renditions.map((r) => `${r.url} ${r.width}w`).join(", ");
}

/**
 * JSON for a `<script type="application/ld+json">`: `<` is escaped so text coming from the
 * tenant (product names, descriptions) can never close the script tag.
 */
export function jsonLd(value: unknown): string {
  return JSON.stringify(value).replace(/</g, "\\u003c");
}

export const AVAILABILITY_LABEL: Record<Availability, string> = {
  available: "Disponível",
  sold_out: "Esgotado",
  made_to_order: "Sob encomenda",
};

export const SCHEMA_AVAILABILITY: Record<Availability, string> = {
  available: "https://schema.org/InStock",
  sold_out: "https://schema.org/OutOfStock",
  made_to_order: "https://schema.org/PreOrder",
};
