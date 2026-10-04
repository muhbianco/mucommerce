import { expect, type Locator, type Page, test } from "@playwright/test";

import { FAKE_GOOGLE, STORE } from "./playwright.config";

/**
 * Frete v2, F6 (docs/13-frete-v2.md §8): frete por CEP sem login, na página do produto e no
 * carrinho. A loja do E2E cota com a transportadora fake; a Camiseta tem peso e medidas no seed.
 *
 * Roda depois de enderecos.spec (ordem alfabética, um banco só): a Bia já tem o endereço
 * "trabalho" (CEP 01310-100), que é onde a estimativa e o carrinho logado são comparados.
 */
test.describe.configure({ mode: "serial" });

const CAMISETA = `${STORE}/loja/produto/camiseta-muhbianco`;
const BIA = { sub: "e2e-bia", email: "bia.e2e@example.com", name: "Bia E2E" };
const CAIO = { sub: "e2e-caio", email: "caio.e2e@example.com", name: "Caio E2E" };

function preco(texto: string): string {
  const achado = /R\$\s?[\d.]+,\d{2}/.exec(texto.replace(/ /g, " "));
  expect(achado, `preço em "${texto}"`).toBeTruthy();
  return (achado as RegExpExecArray)[0];
}

async function calcular(page: Page, cep: string): Promise<Locator> {
  const frete = page.locator("#frete");
  await frete.getByLabel("CEP").fill(cep);
  await frete.getByRole("button", { name: "Calcular", exact: true }).click();
  const cepFormatado = cep.includes("-") ? cep : `${cep.slice(0, 5)}-${cep.slice(5)}`;
  await expect(frete.getByText(new RegExp(`CEP ${cepFormatado}`))).toBeVisible();
  // Quem clicou "Calcular" continua vendo o resultado (sem JavaScript, o `#frete` do redirect
  // leva até ele; com JavaScript, a navegação mantém a rolagem).
  await expect(frete.getByText(new RegExp(`CEP ${cepFormatado}`))).toBeInViewport();
  return frete;
}

async function entrar(page: Page, pessoa: typeof BIA, volta: string): Promise<void> {
  await page.request.post(`${FAKE_GOOGLE}/__e2e/identity`, { data: pessoa });
  await page.goto(`${STORE}/entrar?next=${encodeURIComponent(volta)}`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page).toHaveURL(`${STORE}${volta}`);
}

/** Esvazia o carrinho (o estado dele é compartilhado com os specs seguintes). */
async function esvaziar(page: Page): Promise<void> {
  await page.goto(`${STORE}/carrinho`);
  const remover = page.getByRole("button", { name: "Remover" });
  while (await remover.count()) {
    await remover.first().click();
    await page.waitForLoadState("networkidle");
  }
  await expect(page.getByText("Seu carrinho está vazio.")).toBeVisible();
}

test("visitante sem conta calcula o frete pelo CEP, e o CEP vale para os outros produtos", async ({ page }) => {
  await page.goto(CAMISETA);
  await expect(page.getByRole("heading", { name: "Frete e prazo" })).toBeVisible();

  const frete = await calcular(page, "20040-020");
  // O CEP não vai para a URL (dado pessoal): ele fica num cookie.
  expect(page.url()).not.toContain("20040");
  await expect(frete.getByText(/Para 1× Camiseta MuhBianco P · CEP 20040-020/)).toBeVisible();
  const opcoes = frete.getByRole("list", { name: "Opções de frete" });
  await expect(opcoes.getByRole("listitem")).toHaveCount(3);
  await expect(opcoes.locator("li", { hasText: "Fake Econômico" })).toContainText("Mais barato");
  await expect(opcoes.locator("li", { hasText: "Fake Econômico" })).toContainText("chega em 6 a 8 dias úteis");
  await expect(opcoes.locator("li", { hasText: "Fake Expresso" })).toContainText("Mais rápido");
  await expect(opcoes.locator("li", { hasText: "Retirar em Loja MuhBianco" })).toContainText("grátis");

  // Outro tamanho: o seletor manda a variante escolhida junto com o CEP.
  await page.getByRole("radio", { name: "G" }).check();
  await calcular(page, "20040-020");
  await expect(frete.getByText(/Para 1× Camiseta MuhBianco G/)).toBeVisible();
  await expect(page.getByRole("radio", { name: "G" })).toBeChecked();

  // Em outro produto, o CEP já está lá e o frete aparece sem digitar de novo. Este não tem
  // medidas: a tela diz o motivo em vez de um erro genérico.
  await page.goto(`${STORE}/loja`);
  await page.getByRole("link", { name: /Bolo de cenoura/i }).first().click();
  const outro = page.locator("#frete");
  await expect(outro.getByLabel("CEP")).toHaveValue("20040-020");
  await expect(outro.getByText(/ainda não informou o tamanho deste produto/)).toBeVisible();
  await expect(outro.getByText(/Retirar em Loja MuhBianco/)).toBeVisible();
});

test("o preço da estimativa é o mesmo do carrinho com o endereço", async ({ page }) => {
  await entrar(page, BIA, "/carrinho");
  await esvaziar(page);

  await page.goto(CAMISETA);
  const frete = await calcular(page, "01310-100");
  const estimado = preco(
    (await frete.getByRole("list", { name: "Opções de frete" }).locator("li", { hasText: "Fake Econômico" }).innerText()),
  );

  await page.getByRole("radio", { name: "P" }).check();
  await page.getByRole("button", { name: "Comprar", exact: true }).click();
  await expect(page).toHaveURL(/\/carrinho/);
  await page.getByRole("radio", { name: /trabalho/ }).check();
  await page.getByRole("button", { name: "Calcular frete" }).click();
  const cotado = preco(await page.locator("label", { hasText: "Fake Econômico" }).innerText());
  expect(cotado).toBe(estimado);

  await esvaziar(page);
});

test("no carrinho, quem ainda não tem endereço estima pelo CEP", async ({ page }) => {
  await entrar(page, CAIO, "/loja/produto/camiseta-muhbianco");
  await page.getByRole("radio", { name: "P" }).check();
  await page.getByRole("button", { name: "Comprar", exact: true }).click();
  await expect(page).toHaveURL(/\/carrinho/);

  const frete = await calcular(page, "20040-020");
  await expect(frete.getByText(/Para o seu carrinho · CEP 20040-020/)).toBeVisible();
  await expect(frete.getByRole("list", { name: "Opções de frete" }).locator("li", { hasText: "Fake Econômico" })).toBeVisible();
  // Escolher e fechar o pedido continua pedindo o endereço (é lá que a cotação assinada nasce).
  await expect(frete.getByRole("link", { name: "cadastre o endereço de entrega" })).toBeVisible();

  await esvaziar(page);
});

test.describe("sem JavaScript", () => {
  test.use({ javaScriptEnabled: false });

  test("o formulário do CEP funciona com POST puro e volta até o frete", async ({ page }) => {
    await page.goto(CAMISETA);
    const frete = page.locator("#frete");
    await frete.getByLabel("CEP").fill("20040020");
    await frete.getByRole("button", { name: "Calcular", exact: true }).click();
    await expect(page).toHaveURL(/#frete$/);
    await expect(frete.getByText(/Para 1× Camiseta MuhBianco P · CEP 20040-020/)).toBeVisible();
    await expect(frete.getByRole("list", { name: "Opções de frete" }).locator("li", { hasText: "Fake Econômico" })).toBeVisible();
  });
});
