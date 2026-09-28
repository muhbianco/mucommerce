import { describe, expect, it } from "vitest";

import { cartCountLabel, cartCountOf, parseCartCount } from "./cart-count";

describe("contador do carrinho", () => {
  it("lê o número do cookie", () => {
    expect(parseCartCount("3")).toBe(3);
    expect(parseCartCount("0")).toBe(0);
    expect(parseCartCount("999")).toBe(999);
  });

  it("descarta cookie torto em vez de mostrar lixo no cabeçalho", () => {
    expect(parseCartCount(undefined)).toBeNull();
    expect(parseCartCount("")).toBeNull();
    expect(parseCartCount("-1")).toBeNull();
    expect(parseCartCount("1.5")).toBeNull();
    expect(parseCartCount("1000")).toBeNull();
    expect(parseCartCount("<script>")).toBeNull();
  });

  it("encurta número grande, que deformaria o cabeçalho no celular", () => {
    expect(cartCountLabel(7)).toBe("7");
    expect(cartCountLabel(99)).toBe("99");
    expect(cartCountLabel(100)).toBe("99+");
  });

  it("conta linhas, não quantidades", () => {
    // Um quilo e um quarto de castanha é uma linha. Somar quantidades daria "1,25 itens".
    expect(cartCountOf({ items: [{ quantity: "1.250" }] })).toBe(1);
    expect(cartCountOf({ items: [{}, {}, {}] })).toBe(3);
    expect(cartCountOf({ items: [] })).toBe(0);
    expect(cartCountOf(null)).toBe(0);
  });
});
