import { Nunito } from "next/font/google";

/**
 * Fontes da vitrine, servidas pelo próprio app.
 *
 * `next/font` baixa no build e serve de `'self'`, que é o que o CSP da loja permite — mesmo
 * caminho que o painel já usa. Declarar em escopo de módulo é exigência do `next/font`: a
 * família é fixa no build, e o que muda por loja é qual variável CSS entra em cena.
 *
 * `rounded` era `ui-rounded`, que só existe em aparelho da Apple: quem abria no Android via a
 * fonte do sistema e a escolha do lojista não valia nada. Agora é uma fonte de verdade.
 */
const nunito = Nunito({
  subsets: ["latin"], // cobre o português inteiro; `latin-ext` seria peso morto
  weight: ["400", "600", "700", "800"],
  variable: "--store-font-rounded",
  display: "swap",
});

/** As classes de fonte a pendurar na casca da loja. */
export const storeFonts = nunito.variable;
