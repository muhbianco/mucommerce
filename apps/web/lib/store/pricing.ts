/** Contas de preço da vitrine. Tudo puro, para caber no vitest. */

import { formatPrice, type StorePrice } from "../storefront";

/** Estava copiado em `carrinho/page.tsx` e em `checkout/page.tsx`, linha por linha. */
export function money(cents: number, currency: string): string {
  return formatPrice({
    amount_cents: cents,
    compare_at_cents: null,
    promo_active: false,
    promo_ends_at: null,
    currency,
  });
}

/**
 * Quanto por cento o preço caiu, ou `null` quando não vale anunciar.
 *
 * Abaixo de 5% o selo vira ruído: "3% OFF" ocupa o mesmo espaço de um desconto de verdade e
 * ensina o cliente a ignorar o selo. Arredonda para baixo — anunciar 30% num desconto de 29,6%
 * é propaganda enganosa por um décimo.
 */
export function discountPercent(price: StorePrice): number | null {
  const was = price.compare_at_cents;
  if (!was || was <= price.amount_cents) return null;
  const percent = Math.floor(((was - price.amount_cents) / was) * 100);
  return percent >= 5 ? percent : null;
}

/**
 * Quanto falta para o frete sair de graça, ou `null` quando não há o que dizer.
 *
 * Só serve ao envio por transportadora: retirada e entrega por zona têm taxa própria, e
 * prometer frete grátis para quem vai escolher entrega local é promessa falsa.
 */
export function freeShippingGap(subtotalCents: number, freeAboveCents: number | null): number | null {
  if (freeAboveCents === null || freeAboveCents <= 0) return null;
  const missing = freeAboveCents - subtotalCents;
  return missing > 0 ? missing : 0;
}
