import { Montserrat, Nunito, Oswald, Playfair_Display } from "next/font/google";

/**
 * Fontes da vitrine, servidas pelo próprio app.
 *
 * `next/font` baixa no build e serve de `'self'`, que é o que o CSP da loja permite — mesmo
 * caminho que o painel já usa. Declarar em escopo de módulo é exigência do `next/font`: a
 * família é fixa no build, e o que muda por loja é qual variável CSS entra em cena.
 *
 * `rounded` era `ui-rounded`, que só existe em aparelho da Apple: quem abria no Android via a
 * fonte do sistema e a escolha do lojista não valia nada. Agora é uma fonte de verdade.
 *
 * Oswald, Playfair Display e Montserrat entraram com o motor de identidade (docs/16): títulos
 * condensados de impacto, elegantes e geométricos. O navegador só baixa o arquivo de uma família
 * quando alguma letra da página a usa — loja que não escolheu `display` não paga por ela.
 */
const nunito = Nunito({
  subsets: ["latin"], // cobre o português inteiro; `latin-ext` seria peso morto
  weight: ["400", "600", "700", "800"],
  variable: "--store-font-rounded",
  display: "swap",
});

const oswald = Oswald({
  subsets: ["latin"],
  weight: ["500", "600", "700"],
  variable: "--store-font-display",
  display: "swap",
});

const playfair = Playfair_Display({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  variable: "--store-font-elegant",
  display: "swap",
});

const montserrat = Montserrat({
  subsets: ["latin"],
  weight: ["400", "500", "600", "700", "800"],
  variable: "--store-font-geometric",
  display: "swap",
});

/** As classes de fonte a pendurar na casca da loja. */
export const storeFonts = [nunito, oswald, playfair, montserrat].map((f) => f.variable).join(" ");
