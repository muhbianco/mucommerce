/**
 * Frete na vitrine (frete v2, F6): o que a estimativa por CEP devolve e como a tela fala dela.
 * Tudo puro, para caber no vitest.
 */

/** Uma opção da estimativa (`POST /storefront/shipping/estimate`). Sem assinatura: só mostra. */
export interface EstimateOption {
  service_code: string;
  service_name: string;
  carrier: string;
  /** Já com o acréscimo da loja; o frete grátis a tela aplica pelo subtotal. */
  price_cents: number;
  delivery_days: number | null;
  delivery_min?: number | null;
  delivery_max?: number | null;
}

export interface EstimateResult {
  options: EstimateOption[];
  problem: string | null;
  refusals?: string[];
}

/** "01001-000", "01001000", " 01001 000 " → "01001000"; qualquer outra coisa → null. */
export function cepDigits(raw: string | null | undefined): string | null {
  const digits = (raw ?? "").replace(/[\s.-]/g, "");
  return /^\d{8}$/.test(digits) ? digits : null;
}

/** "01001000" → "01001-000". */
export function formatCep(digits: string): string {
  return digits.length === 8 ? `${digits.slice(0, 5)}-${digits.slice(5)}` : digits;
}

/** "chega em 5 a 8 dias úteis", "chega em 2 dias úteis", ou "" quando a transportadora não diz. */
export function deliveryText(option: EstimateOption): string {
  const min = option.delivery_min ?? null;
  const max = option.delivery_max ?? option.delivery_days ?? null;
  if (max === null) return "";
  if (min !== null && min < max) return `chega em ${min} a ${max} dias úteis`;
  return max === 1 ? "chega em 1 dia útil" : `chega em ${max} dias úteis`;
}

/**
 * Selos "Mais barato" e "Mais rápido", por código de serviço. Com uma opção só não há o que
 * comparar; empate fica com a primeira (a API já devolve do mais barato para o mais caro).
 */
export function estimateBadges(options: EstimateOption[]): Record<string, string[]> {
  const selos: Record<string, string[]> = {};
  if (options.length < 2) return selos;
  const add = (code: string, label: string) => {
    (selos[code] ??= []).push(label);
  };
  const barato = options.reduce((a, b) => (b.price_cents < a.price_cents ? b : a));
  add(barato.service_code, "Mais barato");
  const prazo = (o: EstimateOption) => o.delivery_max ?? o.delivery_days ?? Number.POSITIVE_INFINITY;
  const rapido = options.reduce((a, b) => (prazo(b) < prazo(a) ? b : a));
  if (Number.isFinite(prazo(rapido))) add(rapido.service_code, "Mais rápido");
  return selos;
}

/** Por que não há frete para mostrar. `item` muda a frase entre produto e carrinho. */
export function estimateProblemText(problem: string, item: "produto" | "carrinho"): string {
  const daqui = item === "produto" ? "deste produto" : "de um item do carrinho";
  const texts: Record<string, string> = {
    shipping_disabled: "Esta loja não está enviando por transportadora agora.",
    not_configured: "A loja ainda não terminou de configurar o envio.",
    missing_dimensions: `A loja ainda não informou o tamanho ${daqui}. Fale com ela para saber o frete.`,
    unavailable: "A transportadora não respondeu agora. Tente de novo em instantes.",
    no_service: "Nenhuma transportadora entrega nesse CEP.",
    too_many_parcels: "Essa quantidade precisa de mais caixas do que a loja envia de uma vez. Fale com a loja.",
    no_items: item === "produto" ? "Este produto não está à venda agora." : "Nenhum item do carrinho está à venda agora.",
    rate_limited: "Muitas consultas seguidas. Espere um minuto e tente de novo.",
    validation_error: "Confira o CEP: são 8 números.",
  };
  return texts[problem] ?? "Não deu para calcular o frete agora. Tente de novo em instantes.";
}
