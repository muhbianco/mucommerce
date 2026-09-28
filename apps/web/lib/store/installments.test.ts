import { describe, expect, it } from "vitest";

import { bandFor, bestPlan, MIN_INSTALLMENT_CENTS, type PublicPayments, surchargeCents } from "./installments";

/**
 * A tabela abaixo é a mesma de `tests/test_surcharge_mirror.py`, no api-commerce. As duas contas
 * têm de bater centavo a centavo: a de lá cobra, a daqui anuncia, e anunciar diferente do que se
 * cobra é propaganda enganosa, não erro de arredondamento.
 */
export const CASOS_ESPELHADOS = [
  // [base em centavos, pontos-base, fixo em centavos, acréscimo esperado]
  [10_000, 0, 0, 0],
  [10_000, 350, 0, 350],
  [10_000, 0, 99, 99],
  [10_000, 350, 99, 449],
  // Arredonda para cima: 1 é 0,35 centavo, e meio centavo por pedido some no relatório e
  // aparece na conta da loja no fim do mês.
  [1, 350, 0, 1],
  [999, 350, 0, 35],
  [5_990, 450, 0, 270],
] as const;

function pagamentos(over: Partial<PublicPayments> = {}): PublicPayments {
  return {
    methods: ["pix", "card"],
    card_installments_max: 12,
    surcharge: null,
    ...over,
  };
}

describe("acréscimo, espelhando o servidor", () => {
  it.each(CASOS_ESPELHADOS)("base %i, %i bps + %i centavos → %i", (base, bps, fixo, esperado) => {
    const p = pagamentos({ surcharge: { card_installments: [{ up_to: 12, percent_bps: bps, fixed_cents: fixo }] } });
    expect(surchargeCents(p, base, 12)).toBe(esperado);
  });

  it("não cobra nada quando a loja não repassa", () => {
    expect(surchargeCents(pagamentos(), 10_000, 12)).toBe(0);
  });

  it("lê a primeira faixa que cobre o parcelamento", () => {
    const faixas = [
      { up_to: 6, percent_bps: 0, fixed_cents: 0 },
      { up_to: 12, percent_bps: 450, fixed_cents: 0 },
    ];
    expect(bandFor(faixas, 1)?.up_to).toBe(6);
    expect(bandFor(faixas, 6)?.up_to).toBe(6);
    expect(bandFor(faixas, 7)?.up_to).toBe(12);
  });

  it("parcelando além da última faixa, vale a última — a mais cara", () => {
    const faixas = [{ up_to: 6, percent_bps: 450, fixed_cents: 0 }];
    expect(bandFor(faixas, 10)?.percent_bps).toBe(450);
  });

  it("faixa fora de ordem no banco não muda o resultado", () => {
    const desordenado = [
      { up_to: 12, percent_bps: 450, fixed_cents: 0 },
      { up_to: 6, percent_bps: 0, fixed_cents: 0 },
    ];
    expect(bandFor(desordenado, 3)?.up_to).toBe(6);
  });
});

describe("o que a vitrine anuncia", () => {
  it("mostra o maior parcelamento que a loja aceita", () => {
    const plano = bestPlan(pagamentos(), 60_000);
    expect(plano).toEqual({ count: 12, perInstallmentCents: 5_000, totalCents: 60_000, interestFree: true });
  });

  it("diz que tem juros quando a loja repassa", () => {
    const p = pagamentos({
      surcharge: {
        card_installments: [
          { up_to: 6, percent_bps: 0, fixed_cents: 0 },
          { up_to: 12, percent_bps: 450, fixed_cents: 0 },
        ],
      },
    });
    const plano = bestPlan(p, 60_000)!;
    expect(plano.count).toBe(12);
    expect(plano.interestFree).toBe(false);
    expect(plano.totalCents).toBe(62_700);
    expect(plano.perInstallmentCents).toBe(5_225);
  });

  it("arredonda a parcela para cima: doze vezes não pode somar menos que o total", () => {
    const plano = bestPlan(pagamentos(), 10_000)!;
    expect(plano.perInstallmentCents * plano.count).toBeGreaterThanOrEqual(plano.totalCents);
  });

  it("desce o número de parcelas até a parcela valer a pena", () => {
    // R$ 30,00 em 12x daria R$ 2,50 — abaixo do piso. Anuncia 6x de R$ 5,00.
    const plano = bestPlan(pagamentos(), 3_000)!;
    expect(plano.count).toBe(6);
    expect(plano.perInstallmentCents).toBe(MIN_INSTALLMENT_CENTS);
  });

  it("cala a boca quando não há o que anunciar", () => {
    expect(bestPlan(null, 10_000), "sem dado nenhum (API antiga, meio do deploy)").toBeNull();
    expect(bestPlan(pagamentos({ methods: ["pix"] }), 10_000), "loja só com Pix").toBeNull();
    expect(bestPlan(pagamentos({ card_installments_max: 1 }), 10_000), "cartão sem parcelar").toBeNull();
    expect(bestPlan(pagamentos(), 900), "R$ 9,00 não parcela em lugar nenhum").toBeNull();
    expect(bestPlan(pagamentos(), 0), "preço zero").toBeNull();
  });

  it("nunca passa de doze, mesmo se o dado vier torto", () => {
    expect(bestPlan(pagamentos({ card_installments_max: 99 }), 600_000)!.count).toBe(12);
  });
});
