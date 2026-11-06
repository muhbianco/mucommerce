import localFont from "next/font/local";

/**
 * Fontes da vitrine, servidas pelo próprio app a partir de arquivos do repositório.
 *
 * Servidas de `'self'`, que é o que o CSP da loja permite. Declarar em escopo de módulo é
 * exigência do `next/font`: a família é fixa no build, e o que muda por loja é qual variável CSS
 * entra em cena.
 *
 * **Por que arquivo local, e não `next/font/google`.** O `next/font/google` baixa o CSS e as
 * fontes do Google *durante o build*. Com quatro famílias, o deploy de 06/11/2026 caiu duas vezes
 * em quatro builds com `Cannot read properties of null (reading '1')` no carregador — resposta do
 * Google fora do formato esperado, ora na Oswald, ora na Montserrat. Um deploy não pode depender
 * de rede de terceiro no meio do build. Os arquivos em `fonts/` são o subconjunto latino em versão
 * variável (um arquivo cobre todos os pesos), baixados do CDN do Google Fonts, com a licença OFL
 * de cada família ao lado.
 *
 * `rounded` era `ui-rounded`, que só existe em aparelho da Apple: quem abria no Android via a
 * fonte do sistema e a escolha do lojista não valia nada. Agora é uma fonte de verdade.
 *
 * Oswald, Playfair Display e Montserrat entraram com o motor de identidade (docs/16): títulos
 * condensados de impacto, elegantes e geométricos. Sem pré-carregamento: o navegador só baixa o
 * arquivo de uma família quando alguma letra da página a usa — loja que não escolheu `display`
 * não paga por ela.
 */
const nunito = localFont({
  src: "./fonts/nunito-latin.woff2",
  weight: "200 1000",
  variable: "--store-font-rounded",
  display: "swap",
});

const oswald = localFont({
  src: "./fonts/oswald-latin.woff2",
  weight: "200 700",
  variable: "--store-font-display",
  display: "swap",
  // Só a loja que escolheu esta família baixa o arquivo: pré-carregar faria toda loja pagar.
  preload: false,
});

const playfair = localFont({
  src: "./fonts/playfair-display-latin.woff2",
  weight: "400 900",
  variable: "--store-font-elegant",
  display: "swap",
  // Só a loja que escolheu esta família baixa o arquivo: pré-carregar faria toda loja pagar.
  preload: false,
});

const montserrat = localFont({
  src: "./fonts/montserrat-latin.woff2",
  weight: "100 900",
  variable: "--store-font-geometric",
  display: "swap",
  // Só a loja que escolheu esta família baixa o arquivo: pré-carregar faria toda loja pagar.
  preload: false,
});

/** As classes de fonte a pendurar na casca da loja. */
export const storeFonts = [nunito, oswald, playfair, montserrat].map((f) => f.variable).join(" ");
