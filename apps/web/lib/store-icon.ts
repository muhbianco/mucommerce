/**
 * Ícone da aba e do resultado de busca de cada loja (pure: tested with vitest).
 *
 * O Google só mostra ícone quadrado, e o logotipo das lojas costuma ser horizontal (o da SG
 * Pipas é 3:2) e vem em WebP, que o gerador de imagem do Next não lê. Por isso o ícone é a
 * sigla da loja sobre a cor da marca: sai sempre, legível em 16 px, e muda sozinho quando o
 * lojista troca o nome ou a cor.
 */

/** Tamanho servido: múltiplo de 48 px, como o Google pede, e grande o bastante para o iOS. */
export const ICON_SIZE = 192;

/**
 * "SG Pipas" → "SG", "Bolso Coberto" → "BC", "Lunares" → "L".
 *
 * Primeira palavra curta e toda em maiúsculas é sigla e vale sozinha; senão, a inicial das
 * duas primeiras palavras. Palavra sem letra nem número (um "&", um "-") não conta.
 */
export function monogram(name: string): string {
  const words = name
    .trim()
    .split(/\s+/)
    .map((word) => word.replace(/[^\p{L}\p{N}]/gu, ""))
    .filter(Boolean);
  const first = words[0];
  if (!first) return "?";
  if (first.length <= 3 && first === first.toLocaleUpperCase("pt-BR") && /\p{L}/u.test(first)) return first;
  return words
    .slice(0, 2)
    .map((word) => [...word][0] ?? "")
    .join("")
    .toLocaleUpperCase("pt-BR");
}

/**
 * Versão do ícone para o `?v=` do link: muda quando muda o que ele desenha, e o navegador (que
 * guarda favicon por muito tempo) busca o novo.
 */
export function iconVersion(name: string, background: string, foreground: string): string {
  let hash = 5381;
  for (const char of `${name}|${background}|${foreground}`) {
    hash = ((hash * 33) ^ (char.codePointAt(0) ?? 0)) >>> 0;
  }
  return hash.toString(36);
}
