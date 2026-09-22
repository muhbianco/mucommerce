import { describe, expect, it } from "vitest";

import { normalizeEmail, normalizeInstagram, normalizeWhatsapp } from "./contact";

describe("normalizeWhatsapp", () => {
  it("accepts what people type and answers in E.164", () => {
    expect(normalizeWhatsapp("(11) 99999-9999")).toBe("+5511999999999");
    expect(normalizeWhatsapp("11999999999")).toBe("+5511999999999");
    expect(normalizeWhatsapp("+55 11 99999-9999")).toBe("+5511999999999");
    expect(normalizeWhatsapp("1133334444")).toBe("+551133334444"); // landline, 10 digits
    expect(normalizeWhatsapp("+1 415 555 0132")).toBe("+14155550132"); // already international
  });

  it("refuses what cannot be a number", () => {
    expect(normalizeWhatsapp("")).toBeNull();
    expect(normalizeWhatsapp("liga pra mim")).toBeNull();
    expect(normalizeWhatsapp("99999")).toBeNull(); // too short even with +55
    expect(normalizeWhatsapp("+0 11 99999-9999")).toBeNull(); // country code cannot start with 0
  });
});

describe("normalizeInstagram", () => {
  it("keeps only the handle", () => {
    expect(normalizeInstagram("@loja")).toBe("loja");
    expect(normalizeInstagram("loja.doces_1")).toBe("loja.doces_1");
    expect(normalizeInstagram("https://www.instagram.com/loja/")).toBe("loja");
    expect(normalizeInstagram("instagram.com/loja?hl=pt")).toBe("loja");
  });

  it("refuses a handle with characters Instagram does not allow", () => {
    expect(normalizeInstagram("")).toBeNull();
    expect(normalizeInstagram("minha loja")).toBeNull();
    expect(normalizeInstagram("a".repeat(31))).toBeNull();
  });
});

describe("normalizeEmail", () => {
  it("trims and lowercases", () => {
    expect(normalizeEmail("  Contato@Loja.com.BR ")).toBe("contato@loja.com.br");
  });

  it("refuses an address the API would reject", () => {
    expect(normalizeEmail("contato@loja")).toBeNull();
    expect(normalizeEmail("contato loja@x.com")).toBeNull();
    expect(normalizeEmail(`${"a".repeat(250)}@loja.com.br`)).toBeNull();
  });
});
