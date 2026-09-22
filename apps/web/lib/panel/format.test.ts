import { describe, expect, it } from "vitest";

import {
  formatQuantity,
  localToUtcIso,
  moneyInput,
  parseModifierLines,
  parseMoney,
  parseQuantity,
  utcToLocalInput,
} from "./format";

describe("money", () => {
  it("parses what people type", () => {
    expect(parseMoney("12,50")).toBe(1250);
    expect(parseMoney("12.50")).toBe(1250);
    expect(parseMoney("1.234,56")).toBe(123456);
    expect(parseMoney("R$ 7")).toBe(700);
    expect(parseMoney("")).toBeNull();
    expect(parseMoney("12,555")).toBeNaN();
    expect(parseMoney("-3")).toBeNaN();
    expect(parseMoney("abc")).toBeNaN();
  });

  it("round-trips through the input format", () => {
    expect(moneyInput(1250)).toBe("12,50");
    expect(parseMoney(moneyInput(99999))).toBe(99999);
    expect(moneyInput(null)).toBe("");
  });
});

describe("tenant-zoned promotion dates", () => {
  it("converts São Paulo wall time to UTC and back", () => {
    expect(localToUtcIso("2026-10-01T09:30", "America/Sao_Paulo")).toBe("2026-10-01T12:30:00.000Z");
    expect(utcToLocalInput("2026-10-01T12:30:00Z", "America/Sao_Paulo")).toBe("2026-10-01T09:30");
    expect(localToUtcIso("", "America/Sao_Paulo")).toBeNull();
    expect(localToUtcIso("amanhã", "America/Sao_Paulo")).toBe("invalid");
  });

  it("follows the zone, not the server clock", () => {
    expect(localToUtcIso("2026-07-01T12:00", "Europe/Lisbon")).toBe("2026-07-01T11:00:00.000Z");
  });
});

describe("quantities", () => {
  it("parses and formats", () => {
    expect(parseQuantity("2,5")).toBe("2.5");
    expect(parseQuantity("-3")).toBe("-3");
    expect(parseQuantity("1.2345")).toBeNull();
    expect(formatQuantity("12.000", "un")).toBe("12 un");
    expect(formatQuantity("250.500", "g")).toBe("250,5 g");
  });
});


describe("modifier lines", () => {
  it("reads one modifier per line, price optional", () => {
    expect(parseModifierLines("Chocolate = 3,50\n\n  Sem cobertura  \nVela=1")).toEqual([
      { name: "Chocolate", price_cents: 350 },
      { name: "Sem cobertura", price_cents: 0 },
      { name: "Vela", price_cents: 100 },
    ]);
  });

  it("refuses lines it cannot read", () => {
    expect(parseModifierLines("Chocolate = três")).toBeNull();
    expect(parseModifierLines("= 3,00")).toBeNull();
    expect(parseModifierLines("a = 1 = 2")).toBeNull();
  });
});
