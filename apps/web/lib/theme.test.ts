import { describe, expect, it } from "vitest";

import { onColor, themeVariables } from "./theme";

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
