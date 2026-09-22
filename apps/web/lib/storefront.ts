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

/** `unavailable`: paused by the store (published, not for sale for now). */
export type Availability = "available" | "sold_out" | "made_to_order" | "unavailable";

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

export interface TagRef {
  slug: string;
  name: string;
}

export interface ProductOption {
  name: string;
  values: string[];
}

export interface StoreVariant {
  id: string;
  sku: string;
  name: string;
  option_values: Record<string, string> | null;
  price: StorePrice;
  availability: Availability;
}

export interface ProductDetail extends ProductCard {
  sku: string;
  options: ProductOption[];
  description_md: string | null;
  unit_label: string;
  sold_by: string;
  variants: StoreVariant[];
  images: StoreImage[];
  categories: CategoryRef[];
  tags: TagRef[];
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
  unavailable: "Indisponível",
};

export const SCHEMA_AVAILABILITY: Record<Availability, string> = {
  available: "https://schema.org/InStock",
  sold_out: "https://schema.org/OutOfStock",
  made_to_order: "https://schema.org/PreOrder",
  unavailable: "https://schema.org/OutOfStock",
};

/** The variant with exactly this combination of option values (none: not offered). */
export function findVariant(variants: StoreVariant[], selection: Record<string, string>): StoreVariant | undefined {
  return variants.find((variant) => {
    const values = variant.option_values ?? {};
    const names = Object.keys(values);
    return names.length === Object.keys(selection).length && names.every((name) => selection[name] === values[name]);
  });
}

/** schema.org offers: one Offer, or an AggregateOffer when the variants' prices differ. */
export function productOffers(product: ProductDetail, url: string): Record<string, unknown> {
  const amounts = product.variants.map((variant) => variant.price.amount_cents);
  const low = amounts.length ? Math.min(...amounts) : product.price.amount_cents;
  const high = amounts.length ? Math.max(...amounts) : product.price.amount_cents;
  const availability = SCHEMA_AVAILABILITY[product.availability];
  if (low === high) {
    return {
      "@type": "Offer",
      url,
      price: (product.price.amount_cents / 100).toFixed(2),
      priceCurrency: product.price.currency,
      availability,
      ...(product.price.promo_ends_at ? { priceValidUntil: product.price.promo_ends_at.slice(0, 10) } : {}),
    };
  }
  return {
    "@type": "AggregateOffer",
    url,
    lowPrice: (low / 100).toFixed(2),
    highPrice: (high / 100).toFixed(2),
    offerCount: product.variants.length,
    priceCurrency: product.price.currency,
    availability,
  };
}

/** Not for sale right now (sold out or paused): shown with the muted "sold out" style. */
export function offSale(availability: Availability): boolean {
  return availability === "sold_out" || availability === "unavailable";
}
