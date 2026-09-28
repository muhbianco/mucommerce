/**
 * Quantos itens o carrinho tem, para o número em cima do ícone no cabeçalho.
 *
 * Vem de um cookie, não de uma chamada à API. Toda escrita de carrinho já recebe o carrinho
 * inteiro de volta na resposta, então gravar o número ali custa zero ida ao servidor — enquanto
 * perguntar à API em toda página custaria uma chamada autenticada por visita, inclusive na
 * página de produto, onde o tempo até a primeira pintura é o que decide a venda.
 *
 * O preço disso é que o número pode envelhecer: o item expirou no servidor, ou a pessoa mexeu
 * no carrinho em outro aparelho. É um número em cima de um ícone — quem diz a verdade é
 * `/carrinho`, e o cookie vence sozinho em uma hora.
 *
 * Fica fora de `next/headers` para o middleware (que roda no edge) poder importar o nome.
 */

export const CART_COUNT_COOKIE = "__Host-mb_cart";

/** Uma hora: tempo de sobra para uma compra, e curto o bastante para um número velho sumir. */
export const CART_COUNT_MAX_AGE = 3600;

/** O número gravado, ou `null` se não houver cookie (ou se vier torto). */
export function parseCartCount(raw: string | undefined): number | null {
  if (!raw || !/^\d{1,3}$/.test(raw)) return null;
  const count = Number(raw);
  return count >= 0 && count <= 999 ? count : null;
}

/**
 * O que aparece no ícone. Acima de 99 vira "99+": três dígitos deformam o cabeçalho no celular.
 */
export function cartCountLabel(count: number): string {
  return count > 99 ? "99+" : String(count);
}

/**
 * Quantas linhas o carrinho tem.
 *
 * Linhas, e não a soma das quantidades: produto vendido por peso tem quantidade decimal, e
 * "1,25 itens" não é frase que se mostre a ninguém.
 */
export function cartCountOf(cart: { items?: unknown[] } | null | undefined): number {
  return Math.min(cart?.items?.length ?? 0, 999);
}
