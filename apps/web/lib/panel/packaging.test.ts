import { describe, expect, it } from "vitest";

import { frozenPlanOf } from "./packaging";

describe("frozenPlanOf", () => {
  it("pedido sem plano (motor antigo, retirada) não tem Como embalar", () => {
    expect(frozenPlanOf(null)).toBeNull();
    expect(frozenPlanOf({ service_code: "1" })).toBeNull();
    expect(frozenPlanOf({ parcel_plan: { parcels: [] } })).toBeNull();
    expect(frozenPlanOf({ parcel_plan: "lixo" })).toBeNull();
  });

  it("lê os volumes e o modo de etiqueta do plano congelado", () => {
    const plano = frozenPlanOf({
      parcel_plan: {
        label_mode: "per_volume",
        degraded: false,
        parcels: [{ n: 1, package_name: "Caixa P", items: [{ name: "Rabiola", sku: "", units: 6 }] }],
      },
    });
    expect(plano?.label_mode).toBe("per_volume");
    expect(plano?.parcels).toHaveLength(1);
  });

  it("plano sem modo vira uma etiqueta só", () => {
    expect(frozenPlanOf({ parcel_plan: { parcels: [{ n: 1 }] } })?.label_mode).toBe("single");
  });
});
