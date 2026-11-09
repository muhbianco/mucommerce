import { describe, expect, it } from "vitest";

import { iconVersion, monogram } from "./store-icon";

describe("monogram", () => {
  it("keeps a short all-caps first word as the acronym", () => {
    expect(monogram("SG Pipas")).toBe("SG");
    expect(monogram("MB Loja")).toBe("MB");
  });
  it("takes the initials of the first two words otherwise", () => {
    expect(monogram("Bolso Coberto")).toBe("BC");
    expect(monogram("Lunares")).toBe("L");
    expect(monogram("dream store tabacaria")).toBe("DS");
    expect(monogram("  Ótica   Íris ")).toBe("ÓÍ");
  });
  it("skips words without letters or digits", () => {
    expect(monogram("& Cia Doces")).toBe("CD");
    expect(monogram("Pão & Mel")).toBe("PM");
    expect(monogram("")).toBe("?");
    expect(monogram("— !")).toBe("?");
  });
});

describe("iconVersion", () => {
  it("changes when the name or a color changes, and only then", () => {
    const base = iconVersion("SG Pipas", "#1b6865", "#ffffff");
    expect(iconVersion("SG Pipas", "#1b6865", "#ffffff")).toBe(base);
    expect(iconVersion("SG Pipas!", "#1b6865", "#ffffff")).not.toBe(base);
    expect(iconVersion("SG Pipas", "#1b6866", "#ffffff")).not.toBe(base);
    expect(iconVersion("SG Pipas", "#1b6865", "#000000")).not.toBe(base);
    expect(base).toMatch(/^[0-9a-z]+$/);
  });
});
