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
  /** "ticket": the product's page is its event (/eventos/<slug>). */
  kind: string;
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

export interface StoreModifierGroup {
  id: string;
  name: string;
  min_select: number;
  max_select: number;
  modifiers: { id: string; name: string; price_cents: number }[];
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
  modifier_groups: StoreModifierGroup[];
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

/**
 * Fields added after the first release default to empty, so a web newer than the API it talks to
 * (mid-deploy, or a cached answer) still renders the product instead of failing.
 */
export function withDefaults(product: ProductDetail): ProductDetail {
  return {
    ...product,
    options: product.options ?? [],
    modifier_groups: product.modifier_groups ?? [],
    tags: product.tags ?? [],
    kind: product.kind ?? "physical",
    variants: product.variants.map((variant) => ({ ...variant, option_values: variant.option_values ?? null })),
  };
}

/** "(escolha 1)", "(até 2)", "(de 1 a 3)": the rule of a modifier group, for its legend. */
export function modifierRule(group: StoreModifierGroup): string {
  if (group.min_select === group.max_select) return `(escolha ${group.max_select})`;
  if (group.min_select === 0) return `(opcional, até ${group.max_select})`;
  return `(de ${group.min_select} a ${group.max_select})`;
}

/** Sum of the chosen modifiers' prices (display only: the API prices and validates orders). */
export function modifiersTotal(groups: StoreModifierGroup[], chosen: readonly string[]): number {
  const wanted = new Set(chosen);
  return groups.flatMap((group) => group.modifiers).reduce((sum, m) => sum + (wanted.has(m.id) ? m.price_cents : 0), 0);
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

// ------------------------------------------------------------------ events
export type EventAvailability =
  | "on_sale"
  | "upcoming"
  | "sold_out"
  | "ended"
  | "unavailable"
  | "postponed"
  | "cancelled";
export type LotState = "upcoming" | "on_sale" | "sold_out" | "ended" | "unavailable";

export interface EventCard {
  slug: string;
  name: string;
  short_description: string | null;
  image: StoreImage | null;
  starts_at: string;
  ends_at: string | null;
  venue_name: string | null;
  city: string | null;
  online: boolean;
  availability: EventAvailability;
  price_from: StorePrice | null;
}

export interface EventLotOffer {
  id: string;
  name: string;
  price: StorePrice;
  state: LotState;
  sales_starts_at: string | null;
  sales_ends_at: string | null;
}

export interface EventDetail extends EventCard {
  sku: string;
  description_md: string | null;
  venue_address: string | null;
  status_note: string | null;
  images: StoreImage[];
  lots: EventLotOffer[];
  seo: { title: string | null; description: string | null };
  updated_at: string;
}

export interface EventPage {
  items: EventCard[];
  next_cursor: string | null;
}

export const EVENT_AVAILABILITY_LABEL: Record<EventAvailability, string> = {
  on_sale: "Ingressos à venda",
  upcoming: "Vendas em breve",
  sold_out: "Esgotado",
  ended: "Encerrado",
  unavailable: "Indisponível",
  postponed: "Adiado",
  cancelled: "Cancelado",
};

export const LOT_STATE_LABEL: Record<LotState, string> = {
  on_sale: "À venda",
  upcoming: "Em breve",
  sold_out: "Esgotado",
  ended: "Encerrado",
  unavailable: "Indisponível",
};

const LOT_SCHEMA_AVAILABILITY: Record<LotState, string> = {
  on_sale: "https://schema.org/InStock",
  upcoming: "https://schema.org/PreSale",
  sold_out: "https://schema.org/SoldOut",
  ended: "https://schema.org/Discontinued",
  unavailable: "https://schema.org/Discontinued",
};

/** "sábado, 5 de dezembro de 2026 às 20:00", plus "até 23:00" (same day) or the end date. */
export function formatEventDate(startsAt: string, endsAt: string | null, timeZone: string): string {
  const full = new Intl.DateTimeFormat("pt-BR", { dateStyle: "full", timeStyle: "short", timeZone });
  const day = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeZone });
  const time = new Intl.DateTimeFormat("pt-BR", { timeStyle: "short", timeZone });
  const start = new Date(startsAt);
  if (!endsAt) return full.format(start);
  const end = new Date(endsAt);
  return day.format(start) === day.format(end)
    ? `${full.format(start)} até ${time.format(end)}`
    : `${full.format(start)} até ${full.format(end)}`;
}

/** schema.org Event: status, attendance mode, place, one Offer per lot, the store as organizer. */
export function eventJsonLd(event: EventDetail, url: string, organizer: { name: string; url: string }): Record<string, unknown> {
  const status =
    event.availability === "postponed"
      ? "https://schema.org/EventPostponed"
      : event.availability === "cancelled"
        ? "https://schema.org/EventCancelled"
        : "https://schema.org/EventScheduled";
  const place = event.venue_name
    ? {
        "@type": "Place",
        name: event.venue_name,
        address: {
          "@type": "PostalAddress",
          ...(event.venue_address ? { streetAddress: event.venue_address } : {}),
          ...(event.city ? { addressLocality: event.city } : {}),
          addressCountry: "BR",
        },
      }
    : null;
  // The real link goes to buyers only: the event page stands for it.
  const virtual = event.online ? { "@type": "VirtualLocation", url } : null;
  const mode =
    place && virtual ? "MixedEventAttendanceMode" : virtual ? "OnlineEventAttendanceMode" : "OfflineEventAttendanceMode";
  return {
    "@context": "https://schema.org",
    "@type": "Event",
    name: event.name,
    description: event.seo.description || event.short_description || undefined,
    startDate: event.starts_at,
    ...(event.ends_at ? { endDate: event.ends_at } : {}),
    eventStatus: status,
    eventAttendanceMode: `https://schema.org/${mode}`,
    location: [place, virtual].filter(Boolean),
    image: event.images.map((image) => image.renditions[image.renditions.length - 1]?.url).filter(Boolean),
    organizer: { "@type": "Organization", ...organizer },
    offers: event.lots.map((lot) => ({
      "@type": "Offer",
      name: lot.name,
      url,
      price: (lot.price.amount_cents / 100).toFixed(2),
      priceCurrency: lot.price.currency,
      availability: LOT_SCHEMA_AVAILABILITY[lot.state],
      ...(lot.sales_starts_at ? { validFrom: lot.sales_starts_at } : {}),
    })),
  };
}
