import { describe, expect, it } from "vitest";

import { AVAILABILITY_LABEL, isIndexable, jsonLd, offSale, SCHEMA_AVAILABILITY, srcSet } from "./storefront";
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
