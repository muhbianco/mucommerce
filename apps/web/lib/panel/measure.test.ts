import { describe, expect, it } from "vitest";

import { cmInput, dimsLabel, measureWarnings, parseCm, parseWeight, weightInput, weightLabel } from "./measure";

describe("centímetros", () => {
  it("lê o jeito que a lojista digita", () => {
    expect(parseCm("30")).toBe(300);
    expect(parseCm("30,5")).toBe(305);
    expect(parseCm(" 30.5 ")).toBe(305);
    expect(parseCm("")).toBeNull();
    expect(parseCm("30cm")).toBeNaN();
    expect(parseCm("-3")).toBeNaN();
  });

  it("escreve de volta sem casa decimal à toa", () => {
    expect(cmInput(300)).toBe("30");
    expect(cmInput(305)).toBe("30,5");
    expect(cmInput(null)).toBe("");
    expect(dimsLabel([300, 200, 150])).toBe("30 × 20 × 15 cm");
    expect(dimsLabel([300, null, 150])).toBe("");
  });
});

describe("peso", () => {
  it("converte na unidade escolhida, sempre para cima", () => {
    expect(parseWeight("150", "g")).toBe(150);
    expect(parseWeight("1,2", "kg")).toBe(1200);
    expect(parseWeight("0,1505", "kg")).toBe(151);
    expect(parseWeight("", "kg")).toBeNull();
    expect(parseWeight("um quilo", "kg")).toBeNaN();
  });

  it("mostra em gramas abaixo de 1 kg", () => {
    expect(weightInput(150)).toEqual({ value: "150", unit: "g" });
    expect(weightInput(1250)).toEqual({ value: "1,25", unit: "kg" });
    expect(weightLabel(1250)).toBe("1,25 kg");
    expect(weightLabel(80)).toBe("80 g");
  });
});

describe("avisos de sanidade", () => {
  it("pega o erro de unidade e o limite dos Correios", () => {
    expect(measureWarnings(150, [100, 100, 50])).toEqual([]);
    expect(measureWarnings(150, [3000, 100, 50])).toEqual(["lado_enorme"]);
    expect(measureWarnings(31_000, [100, 100, 3])).toEqual(["lado_minusculo", "acima_30kg"]);
  });
});
