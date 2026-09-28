import { describe, expect, it } from "vitest";

import { contrast } from "./color";
import { onColor, storeTheme, themeVariables } from "./theme";

describe("store theme", () => {
  it("picks readable text on the brand colour", () => {
    expect(onColor("#111111")).toBe("#ffffff");
    expect(onColor("#ffd400")).toBe("#000000");
    expect(onColor("#2e7d32")).toBe("#ffffff");
    expect(onColor("nope")).toBe("#ffffff");
  });

  it("falls back to the primary colour and the system font", () => {
    const vars = themeVariables({ primary_color: "#2e7d32" });
    expect(vars["--brand-secondary"]).toBe("#2e7d32");
    expect(vars["--brand-font"]).toContain("system-ui");
    expect(themeVariables({ primary_color: "red", font: "serif" })["--brand-primary"]).toBe("#111111");
    expect(themeVariables({ font: "serif" })["--brand-font"]).toContain("Georgia");
  });
});

/** Cores que um lojista real escolhe e que costumam quebrar um tema. */
const HOSTILE = [
  "#ffffff",
  "#000000",
  "#ffff00",
  "#ffd400",
  "#ff00ff",
  "#0000ff",
  "#00ffff",
  "#8b8b8b",
  "#2e7d32",
  "#e02020",
  "#111111",
  "#f5f5dc",
];

/** Gerador determinístico: o mesmo conjunto de cores em toda máquina, sem dependência. */
function* sweep(count: number): Generator<string> {
  let seed = 20260928;
  for (let i = 0; i < count; i += 1) {
    seed = (seed * 1664525 + 1013904223) >>> 0;
    yield `#${(seed & 0xffffff).toString(16).padStart(6, "0")}`;
  }
}

describe("a paleta se defende da cor que o lojista escolheu", () => {
  const colours = [...HOSTILE, ...sweep(512)];

  it("mantém contraste AA em todo par que carrega texto", () => {
    for (const primary of colours) {
      const t = storeTheme({ primary_color: primary });
      const check = (fg: string, bg: string, min: number, what: string) => {
        const ratio = contrast(fg, bg);
        expect(ratio, `${what} com a marca ${primary}: ${fg} sobre ${bg} = ${ratio.toFixed(2)}`).toBeGreaterThanOrEqual(min - 1e-6);
      };
      check(t.onBrand, t.brand, 4.5, "texto sobre a marca");
      check(t.brandInk, t.surface, 4.5, "marca como texto");
      check(t.onBrandTint, t.brandTint, 4.5, "texto no selo da marca");
      check(t.accentInk, t.surface, 4.5, "destaque como texto");
      check(t.ink, t.paper, 7, "corpo de texto");
      check(t.inkSoft, t.paper, 4.5, "texto secundário");
      check(t.muted, t.paper, 4.5, "texto apagado");
      check(t.okInk, t.okTint, 4.5, "texto de sucesso");
      check(t.warnInk, t.warnTint, 4.5, "texto de aviso");
      check(t.dangerInk, t.dangerTint, 4.5, "texto de erro");
      // Borda e anel de foco não são texto: a WCAG pede 3:1.
      check(t.line, t.paper, 1.2, "linha visível");
      check(t.brandInk, t.paper, 3, "anel de foco");
    }
  });

  it("nunca emite NaN nem cor malformada", () => {
    for (const primary of colours) {
      for (const [key, value] of Object.entries(themeVariables({ primary_color: primary }))) {
        expect(value, `${key} com a marca ${primary}`).not.toMatch(/NaN|undefined/);
        if (key.endsWith("-rgb")) expect(value).toMatch(/^\d{1,3} \d{1,3} \d{1,3}$/);
        else if (key !== "--brand-font") expect(value).toMatch(/^#[0-9a-f]{6}$/);
      }
    }
  });

  it("afasta o vermelho de erro quando a marca é vermelha", () => {
    const vermelha = storeTheme({ primary_color: "#e02020" });
    const verde = storeTheme({ primary_color: "#2e7d32" });
    // Com marca vermelha o erro sai do lugar; com marca verde ele fica onde sempre esteve.
    expect(vermelha.danger).not.toBe(verde.danger);
    expect(contrast(vermelha.dangerInk, vermelha.dangerTint)).toBeGreaterThanOrEqual(4.5);
  });

  it("congela a paleta de uma marca conhecida", () => {
    // Snapshot de propósito: mexer na receita tem que aparecer no diff da revisão.
    expect(storeTheme({ primary_color: "#2e7d32" })).toMatchInlineSnapshot(`
      {
        "accent": "#2e7d32",
        "accentInk": "#2e7d32",
        "brand": "#2e7d32",
        "brandHover": "#1b6d22",
        "brandInk": "#2e7d32",
        "brandRgb": "46 125 50",
        "brandTint": "#e2fce1",
        "danger": "#af3d36",
        "dangerInk": "#af3d36",
        "dangerTint": "#ffedeb",
        "ink": "#152215",
        "inkRgb": "21 34 21",
        "inkSoft": "#404b3f",
        "line": "#d2dcd2",
        "mist": "#ebf3ea",
        "muted": "#697369",
        "ok": "#007b67",
        "okInk": "#007b67",
        "okTint": "#d3fcf1",
        "onBrand": "#ffffff",
        "onBrandTint": "#2e7d32",
        "paper": "#f7fcf7",
        "surface": "#ffffff",
        "warn": "#886100",
        "warnInk": "#886100",
        "warnTint": "#fff0d5",
      }
    `);
  });
});
