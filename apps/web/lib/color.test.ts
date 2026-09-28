import { describe, expect, it } from "vitest";

import { contrast, fromOklch, luminance, mix, parseHex, toOklch, towardContrast, withLightness } from "./color";

/** Cores que costumam quebrar um sistema de tema: extremos, puras e cinza. */
const HOSTILE = [
  "#ffffff",
  "#000000",
  "#808080",
  "#ffff00",
  "#ffd400",
  "#ff00ff",
  "#0000ff",
  "#00ffff",
  "#ff0000",
  "#2e7d32",
  "#111111",
  "#e02020",
];

describe("cor", () => {
  it("lê e recusa hexadecimal", () => {
    expect(parseHex("#ffffff")).toEqual([1, 1, 1]);
    expect(parseHex("#000000")).toEqual([0, 0, 0]);
    expect(parseHex("vermelho")).toBeNull();
    expect(parseHex("#fff")).toBeNull();
  });

  it("volta de OKLCH para a mesma cor", () => {
    for (const hex of HOSTILE) {
      const back = fromOklch(toOklch(hex));
      const [r, g, b] = parseHex(hex)!;
      const [r2, g2, b2] = parseHex(back)!;
      // Meio passo de 8 bits em cada canal: ida e volta por matriz não é exata.
      expect(Math.abs(r - r2), `${hex} → ${back}`).toBeLessThanOrEqual(2 / 255);
      expect(Math.abs(g - g2), `${hex} → ${back}`).toBeLessThanOrEqual(2 / 255);
      expect(Math.abs(b - b2), `${hex} → ${back}`).toBeLessThanOrEqual(2 / 255);
    }
  });

  it("não devolve NaN para cinza, branco e preto", () => {
    for (const hex of ["#ffffff", "#000000", "#808080"]) {
      const { l, c, h } = toOklch(hex);
      expect(Number.isFinite(l) && Number.isFinite(c) && Number.isFinite(h), hex).toBe(true);
      expect(h, `matiz de cinza é 0, não lixo (${hex})`).toBe(0);
      expect(fromOklch({ l, c, h })).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it("mede contraste como a WCAG", () => {
    expect(contrast("#000000", "#ffffff")).toBeCloseTo(21, 5);
    expect(contrast("#ffffff", "#000000")).toBeCloseTo(21, 5);
    expect(contrast("#777777", "#777777")).toBeCloseTo(1, 5);
    expect(luminance("#ffffff")).toBeCloseTo(1, 5);
    expect(luminance("#000000")).toBeCloseTo(0, 5);
  });

  it("muda a claridade sem perder o matiz", () => {
    const claro = withLightness("#2e7d32", 0.9);
    const escuro = withLightness("#2e7d32", 0.3);
    expect(luminance(claro)).toBeGreaterThan(luminance(escuro));
    expect(Math.abs(toOklch(claro).h - toOklch("#2e7d32").h)).toBeLessThan(6);
  });

  it("mistura nos dois extremos e no meio", () => {
    expect(mix("#000000", "#ffffff", 0)).toBe("#000000");
    expect(mix("#000000", "#ffffff", 1)).toBe("#ffffff");
    const meio = mix("#000000", "#ffffff", 0.5);
    expect(luminance(meio)).toBeGreaterThan(0.1);
    expect(luminance(meio)).toBeLessThan(0.5);
  });

  it("chega ao contraste pedido, ou satura no extremo", () => {
    for (const hex of HOSTILE) {
      for (const fundo of ["#ffffff", "#111111"]) {
        const ajustada = towardContrast(hex, fundo, 4.5);
        const atingiu = contrast(ajustada, fundo) >= 4.5 - 1e-6;
        const saturou = ajustada === "#000000" || ajustada === "#ffffff";
        expect(atingiu || saturou, `${hex} sobre ${fundo} virou ${ajustada}`).toBe(true);
      }
    }
  });

  it("não mexe na cor que já tem contraste de sobra", () => {
    expect(towardContrast("#000000", "#ffffff", 4.5)).toBe("#000000");
    expect(towardContrast("#ffffff", "#000000", 4.5)).toBe("#ffffff");
  });
});
