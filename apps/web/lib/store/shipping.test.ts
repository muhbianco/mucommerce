import { describe, expect, it } from "vitest";

import { cepDigits, deliveryText, type EstimateOption, estimateBadges, estimateProblemText, formatCep } from "./shipping";

const pac: EstimateOption = {
  service_code: "1",
  service_name: "PAC",
  carrier: "Correios",
  price_cents: 1890,
  delivery_days: 8,
  delivery_min: 5,
  delivery_max: 8,
};
const sedex: EstimateOption = { ...pac, service_code: "2", service_name: "SEDEX", price_cents: 3240, delivery_days: 3, delivery_min: 2, delivery_max: 3 };

describe("CEP", () => {
  it("aceita com e sem traço, e recusa o resto", () => {
    expect(cepDigits("01001-000")).toBe("01001000");
    expect(cepDigits(" 01001000 ")).toBe("01001000");
    expect(cepDigits("01001.000")).toBe("01001000");
    expect(cepDigits("0100100")).toBeNull();
    expect(cepDigits("010010000")).toBeNull();
    expect(cepDigits("abcde-fgh")).toBeNull();
    expect(cepDigits(null)).toBeNull();
  });

  it("formata com traço", () => {
    expect(formatCep("01001000")).toBe("01001-000");
  });
});

describe("prazo", () => {
  it("faixa, dia único e sem prazo", () => {
    expect(deliveryText(pac)).toBe("chega em 5 a 8 dias úteis");
    expect(deliveryText({ ...pac, delivery_min: 3, delivery_max: 3 })).toBe("chega em 3 dias úteis");
    expect(deliveryText({ ...pac, delivery_min: null, delivery_max: null, delivery_days: 1 })).toBe("chega em 1 dia útil");
    expect(deliveryText({ ...pac, delivery_min: null, delivery_max: null, delivery_days: null })).toBe("");
  });
});

describe("selos", () => {
  it("o mais barato e o mais rápido", () => {
    expect(estimateBadges([pac, sedex])).toEqual({ "1": ["Mais barato"], "2": ["Mais rápido"] });
  });

  it("o mesmo serviço pode ganhar os dois", () => {
    expect(estimateBadges([{ ...sedex, price_cents: 1000 }, pac])).toEqual({ "2": ["Mais barato", "Mais rápido"] });
  });

  it("uma opção só não ganha selo", () => {
    expect(estimateBadges([pac])).toEqual({});
  });
});

describe("motivos", () => {
  it("fala do produto ou do carrinho", () => {
    expect(estimateProblemText("missing_dimensions", "produto")).toMatch(/deste produto/);
    expect(estimateProblemText("missing_dimensions", "carrinho")).toMatch(/item do carrinho/);
    expect(estimateProblemText("qualquer_coisa", "produto")).toMatch(/Tente de novo/);
  });
});
