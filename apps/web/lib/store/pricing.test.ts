import { describe, expect, it } from "vitest";

import type { StorePrice } from "../storefront";

import { discountPercent, freeShippingBase, freeShippingGap, money } from "./pricing";

function price(amount: number, compareAt: number | null = null): StorePrice {
  return { amount_cents: amount, compare_at_cents: compareAt, promo_active: false, promo_ends_at: null, currency: "BRL" };
}

describe("preço", () => {
  it("escreve em real", () => {
    expect(money(1500, "BRL")).toMatch(/R\$\s*15,00/);
    expect(money(0, "BRL")).toMatch(/R\$\s*0,00/);
  });

  it("calcula o desconto arredondando para baixo", () => {
    // 29,6% não pode virar "30% OFF": é um décimo de propaganda enganosa.
    expect(discountPercent(price(7040, 10000))).toBe(29);
    expect(discountPercent(price(5000, 10000))).toBe(50);
  });

  it("não anuncia desconto que não vale o selo", () => {
    expect(discountPercent(price(9700, 10000)), "3% é ruído").toBeNull();
    expect(discountPercent(price(9500, 10000)), "5% já conta").toBe(5);
    expect(discountPercent(price(1000))).toBeNull();
    expect(discountPercent(price(1000, 1000)), "preço igual não é desconto").toBeNull();
    expect(discountPercent(price(1000, 500)), "riscado menor é erro de cadastro").toBeNull();
  });

  it("diz quanto falta para o frete grátis", () => {
    expect(freeShippingGap(8000, 15000)).toBe(7000);
    expect(freeShippingGap(15000, 15000), "chegou lá").toBe(0);
    expect(freeShippingGap(20000, 15000), "passou, continua grátis").toBe(0);
  });

  it("fica calado quando a loja não oferece frete grátis", () => {
    expect(freeShippingGap(8000, null)).toBeNull();
    expect(freeShippingGap(8000, 0)).toBeNull();
  });
});

describe("freeShippingBase", () => {
  it("desconta o cupom, como o backend", () => {
    expect(freeShippingBase(16000, 2000)).toBe(14000);
    expect(freeShippingGap(freeShippingBase(16000, 2000), 15000), "o cupom tirou do grátis").toBe(1000);
  });

  it("nunca fica negativo", () => {
    expect(freeShippingBase(1000, 5000)).toBe(0);
    expect(freeShippingBase(1000, -10)).toBe(1000);
  });
});
