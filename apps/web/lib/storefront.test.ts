import { describe, expect, it } from "vitest";

import {
  AVAILABILITY_LABEL,
  type EventDetail,
  eventJsonLd,
  findVariant,
  formatEventDate,
  isIndexable,
  jsonLd,
  offSale,
  plainDescription,
  type ProductDetail,
  productOffers,
  SCHEMA_AVAILABILITY,
  srcSet,
  storeJsonLd,
  type StoreVariant,
  withDefaults,
} from "./storefront";
import type { StorefrontContext } from "./tenant";

function context(overrides: Partial<StorefrontContext> = {}, indexable = true): StorefrontContext {
  return {
    tenant: {
      id: "t",
      slug: "loja",
      name: "Loja",
      status: "active",
      timezone: "America/Sao_Paulo",
      locale: "pt-BR",
      currency: "BRL",
    },
    host: "loja.test",
    primary_host: "loja.test",
    access_mode: "public",
    features: { catalog: true },
    branding: {},
    seo: { indexable },
    fulfillment: {},
    ...overrides,
  };
}

describe("indexing", () => {
  it("needs an active, public store that opted in", () => {
    expect(isIndexable(context())).toBe(true);
    expect(isIndexable(context({}, false))).toBe(false);
    expect(isIndexable(context({ access_mode: "whitelist" }))).toBe(false);
    expect(isIndexable(context({ tenant: { ...context().tenant, status: "suspended" } }))).toBe(false);
  });
});

describe("structured data", () => {
  it("cannot be broken out of by tenant text", () => {
    const html = jsonLd({ name: "</script><script>alert(1)</script>" });
    expect(html).not.toContain("</script>");
    expect(JSON.parse(html)).toEqual({ name: "</script><script>alert(1)</script>" });
  });
});

describe("images", () => {
  it("builds a width-described srcset", () => {
    expect(
      srcSet({
        alt: null,
        width: 1200,
        height: 800,
        renditions: [
          { name: "w320", url: "https://s/320.webp", width: 320, height: 213 },
          { name: "orig", url: "https://s/orig.webp", width: 1200, height: 800 },
        ],
      }),
    ).toBe("https://s/320.webp 320w, https://s/orig.webp 1200w");
  });
});

describe("availability", () => {
  it("treats a paused product like a sold-out one, with its own label", () => {
    expect(offSale("unavailable")).toBe(true);
    expect(offSale("sold_out")).toBe(true);
    expect(offSale("made_to_order")).toBe(false);
    expect(AVAILABILITY_LABEL.unavailable).toBe("Indisponível");
    expect(SCHEMA_AVAILABILITY.unavailable).toBe("https://schema.org/OutOfStock");
  });
});

function variant(values: Record<string, string>, cents: number, availability: StoreVariant["availability"] = "available"): StoreVariant {
  const price = { amount_cents: cents, compare_at_cents: null, promo_active: false, promo_ends_at: null, currency: "BRL" };
  return { id: Object.values(values).join("-"), sku: "S", name: "n", option_values: values, price, availability, low_stock: false };
}

describe("variants", () => {
  const variants = [variant({ Tamanho: "P", Cor: "Azul" }, 1000), variant({ Tamanho: "M", Cor: "Azul" }, 1200, "sold_out")];

  it("finds the variant with exactly the chosen combination", () => {
    expect(findVariant(variants, { Tamanho: "M", Cor: "Azul" })?.availability).toBe("sold_out");
    expect(findVariant(variants, { Tamanho: "G", Cor: "Azul" })).toBeUndefined();
    expect(findVariant(variants, { Tamanho: "P" })).toBeUndefined();
  });

  it("uses an AggregateOffer only when prices differ", () => {
    const product = {
      price: variants[0]?.price,
      availability: "available",
      variants,
    } as unknown as ProductDetail;
    expect(productOffers(product, "u")).toMatchObject({ "@type": "AggregateOffer", lowPrice: "10.00", highPrice: "12.00", offerCount: 2 });
    const single = { ...product, variants: variants.slice(0, 1) } as ProductDetail;
    expect(productOffers(single, "u")).toMatchObject({ "@type": "Offer", price: "10.00" });
  });
});

describe("older API answers", () => {
  it("default the fields stage D added", () => {
    const old = { variants: [{ id: "v" }] } as unknown as ProductDetail;
    const product = withDefaults(old);
    expect([product.options, product.modifier_groups, product.tags]).toEqual([[], [], []]);
    expect(product.variants[0]?.option_values).toBeNull();
  });
});

describe("events", () => {
  it("formats the date in the store's time zone, with the end", () => {
    const sameDay = formatEventDate("2026-12-05T23:00:00Z", "2026-12-06T02:00:00Z", "America/Sao_Paulo");
    expect(sameDay).toContain("5 de dezembro de 2026");
    expect(sameDay).toMatch(/20:00.*até 23:00$/);
    expect(formatEventDate("2026-12-05T23:00:00Z", "2026-12-07T02:00:00Z", "America/Sao_Paulo")).toContain(
      "6 de dezembro",
    );
  });

  it("describes status, place and one offer per lot, never the private link", () => {
    const price = { amount_cents: 5000, compare_at_cents: null, promo_active: false, promo_ends_at: null, currency: "BRL" };
    const event = {
      name: "Show",
      short_description: null,
      starts_at: "2026-12-05T23:00:00Z",
      ends_at: null,
      venue_name: "Teatro",
      venue_address: null,
      city: "São Paulo",
      online: true,
      availability: "postponed",
      images: [],
      seo: { title: null, description: null },
      lots: [{ id: "l", name: "1º lote", price, state: "upcoming", sales_starts_at: "2026-11-01T00:00:00Z", sales_ends_at: null }],
    } as unknown as EventDetail;
    const data = eventJsonLd(event, "https://loja/eventos/show", { name: "Loja", url: "https://loja" });
    expect(data).toMatchObject({
      "@type": "Event",
      eventStatus: "https://schema.org/EventPostponed",
      eventAttendanceMode: "https://schema.org/MixedEventAttendanceMode",
      offers: [{ price: "50.00", availability: "https://schema.org/PreSale", validFrom: "2026-11-01T00:00:00Z" }],
    });
    expect(JSON.stringify(data)).toContain('"url":"https://loja/eventos/show"');
  });
});

describe("storeJsonLd", () => {
  const hours = {
    type: "hours",
    address: "Jardim Santo Antonio",
    city: "Osasco",
    state: "SP",
    days: [
      { weekday: 0, opens: "08:00", closes: "12:00" },
      { weekday: 6, opens: "08:00", closes: "17:00" },
      { weekday: 9, opens: "08:00", closes: "17:00" }, // dia que não existe: some
    ],
  };
  const contact = {
    type: "contact",
    whatsapp_e164: "+5511967201770",
    instagram: "sgpipas.original",
    email: "loja@x.test",
  };
  const graphOf = (value: Record<string, unknown>) => value["@graph"] as Record<string, unknown>[];

  it("names the site after the store and links it to the seller", () => {
    const graph = graphOf(storeJsonLd(context({ tenant: { ...context().tenant, name: "SG Pipas" } }), []));
    expect(graph[0]).toMatchObject({
      "@type": "WebSite",
      "@id": "https://loja.test/#site",
      name: "SG Pipas",
      url: "https://loja.test/",
      publisher: { "@id": "https://loja.test/#loja" },
    });
    // Sem endereço na página, a loja é só online.
    expect(graph[1]).toMatchObject({ "@type": "Organization", "@id": "https://loja.test/#loja", name: "SG Pipas" });
    expect(graph[1]).not.toHaveProperty("address");
  });

  it("becomes a Store with what the page already shows: city, hours, phone, Instagram", () => {
    const ctx = context({
      branding: { logo: { url: "https://cdn.test/logo.webp" } },
      seo: { indexable: true, description: "Fábrica de pipas", og_image_url: "https://cdn.test/og.webp" },
    });
    const store = graphOf(storeJsonLd(ctx, [hours, contact]))[1]!;
    expect(store).toMatchObject({
      "@type": "Store",
      logo: "https://cdn.test/logo.webp",
      image: "https://cdn.test/og.webp",
      description: "Fábrica de pipas",
      telephone: "+5511967201770",
      email: "loja@x.test",
      sameAs: ["https://www.instagram.com/sgpipas.original/"],
      address: {
        "@type": "PostalAddress",
        streetAddress: "Jardim Santo Antonio",
        addressLocality: "Osasco",
        addressRegion: "SP",
        addressCountry: "BR",
      },
    });
    expect(store.openingHoursSpecification).toEqual([
      { "@type": "OpeningHoursSpecification", dayOfWeek: "https://schema.org/Monday", opens: "08:00", closes: "12:00" },
      { "@type": "OpeningHoursSpecification", dayOfWeek: "https://schema.org/Sunday", opens: "08:00", closes: "17:00" },
    ]);
  });

  it("uses the primary host, never the one the visit came through", () => {
    const ctx = context({ host: "sgpipas.loja.muhbianco.com.br", primary_host: "sgpipas.com.br" });
    expect(graphOf(storeJsonLd(ctx, []))[0]?.url).toBe("https://sgpipas.com.br/");
  });

  it("cannot close the script tag it is embedded in", () => {
    const ctx = context({ tenant: { ...context().tenant, name: "</script><b>" } });
    expect(jsonLd(storeJsonLd(ctx, []))).not.toContain("</script>");
  });
});

describe("plainDescription", () => {
  it("drops markup and joins lines", () => {
    expect(plainDescription("# Pipa Vanda\r\n\r\n- 25 pipas\n- **50 cm**\n[Veja](https://x.test) ![foto](a.png)")).toBe(
      "Pipa Vanda 25 pipas 50 cm Veja",
    );
  });
  it("cuts at a word and marks the cut", () => {
    const cut = plainDescription("rabiola ".repeat(40), 50) ?? "";
    expect(cut).toBe("rabiola rabiola rabiola rabiola rabiola rabiola…");
    expect(cut.length).toBeLessThanOrEqual(51);
  });
  it("is null when there is nothing to say", () => {
    expect(plainDescription(null)).toBeNull();
    expect(plainDescription("  **  ")).toBeNull();
  });
});
