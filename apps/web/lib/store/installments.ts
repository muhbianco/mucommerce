/**
 * "em até 12x de R$ 24,90 sem juros", na vitrine.
 *
 * Espelha `apps/api-commerce/app/payments/surcharge.py`. É duplicação entre duas linguagens, e
 * é assumida: o servidor precisa da conta para cobrar, a vitrine precisa dela para anunciar
 * antes de existir pedido. Os dois arquivos têm a mesma tabela de casos nos testes; mexer em um
 * sem mexer no outro aparece lá.
 *
 * Errar aqui não é bug de layout, é propaganda enganosa (CDC, e a Lei 13.455/2017 sobre
 * informar o acréscimo antes da escolha). Por isso:
 *   - arredonda sempre para cima, como o Python faz;
 *   - não inventa parcela abaixo do piso que adquirente nenhum aceita;
 *   - e, na dúvida — dado faltando, loja sem meio configurado —, não escreve nada.
 */

/** Faixa de parcelamento do cartão, como a API publica. */
export interface InstallmentBand {
  up_to: number;
  percent_bps: number;
  fixed_cents: number;
}

export interface PublicPayments {
  methods?: string[];
  card_installments_max?: number;
  surcharge?: {
    by_method?: Record<string, { percent_bps: number; fixed_cents: number }>;
    card_installments?: InstallmentBand[];
  } | null;
}

export interface InstallmentPlan {
  /** Em quantas vezes. */
  count: number;
  /** Quanto sai cada parcela, já com o acréscimo. */
  perInstallmentCents: number;
  /** O total pago ao final. Igual ao preço quando não há juros. */
  totalCents: number;
  /** `false` quando a loja repassa a taxa nessa faixa. */
  interestFree: boolean;
}

/**
 * Piso de parcela. Abaixo disso nenhuma maquininha parcela, e "12x de R$ 1,20" só faz a loja
 * parecer desesperada.
 */
export const MIN_INSTALLMENT_CENTS = 500;

/** A faixa que vale para este parcelamento — a primeira que o cobre, como no servidor. */
export function bandFor(bands: InstallmentBand[], installments: number): InstallmentBand | null {
  if (bands.length === 0) return null;
  const ordered = [...bands].sort((a, b) => a.up_to - b.up_to);
  const parcelas = Math.max(1, installments);
  for (const band of ordered) if (parcelas <= band.up_to) return band;
  // Parcelou além da última faixa configurada: vale a última, que é a mais cara.
  return ordered[ordered.length - 1] ?? null;
}

/** Quanto entra a mais por pagar assim. Espelha `surcharge.compute`. */
export function surchargeCents(payments: PublicPayments, baseCents: number, installments: number): number {
  const bands = payments.surcharge?.card_installments ?? [];
  const band = bandFor(bands, installments) ?? payments.surcharge?.by_method?.card ?? null;
  if (!band || baseCents <= 0) return 0;
  const percent = Math.ceil((baseCents * band.percent_bps) / 10_000);
  return Math.max(0, percent + band.fixed_cents);
}

/**
 * O maior parcelamento que a loja pode anunciar para este preço, ou `null` quando não há o que
 * dizer: loja sem cartão, sem parcelamento, ou preço baixo demais para parcelar.
 */
export function bestPlan(payments: PublicPayments | null | undefined, amountCents: number): InstallmentPlan | null {
  if (!payments) return null;
  const max = payments.card_installments_max ?? 1;
  const aceitaCartao = (payments.methods ?? []).includes("card");
  if (!aceitaCartao || max < 2 || amountCents <= 0) return null;

  // Do maior para o menor: anuncia o maior número de vezes que ainda respeita o piso.
  for (let count = Math.min(max, 12); count >= 2; count -= 1) {
    const total = amountCents + surchargeCents(payments, amountCents, count);
    const perInstallment = Math.ceil(total / count);
    if (perInstallment >= MIN_INSTALLMENT_CENTS) {
      return {
        count,
        perInstallmentCents: perInstallment,
        totalCents: total,
        interestFree: total === amountCents,
      };
    }
  }
  return null;
}
