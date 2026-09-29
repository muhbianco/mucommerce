import { expect, type Page, test } from "@playwright/test";

import { FAKE_GOOGLE, STORE } from "./playwright.config";

// Roda depois de customers.spec (um banco semeado só, ordem alfabética): a Bia já entrou e foi
// liberada pela loja lá.
test.describe.configure({ mode: "serial" });

const BIA = { sub: "e2e-bia", email: "bia.e2e@example.com", name: "Bia E2E" };

/**
 * Uma sessão para o arquivo inteiro.
 *
 * O login do cliente tem limite de dez por minuto, e ele é do código, não de configuração —
 * entrar uma vez por teste somava com as outras suítes e derrubava a última. Modo serial já
 * estava ligado, então a página compartilhada é a consequência natural.
 */
let page: Page;

test.beforeAll(async ({ browser, request }) => {
  await request.post(`${FAKE_GOOGLE}/__e2e/identity`, { data: BIA });
  page = await (await browser.newContext()).newPage();
  await page.goto(`${STORE}/entrar?next=%2Fconta%2Fenderecos`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page).toHaveURL(/\/conta\/enderecos/);
});

test.afterAll(async () => {
  await page.close();
});

/** Abre uma tela já autenticada. A busca de CEP só vale depois que a ilha está de pé. */
async function abrir(path: string): Promise<void> {
  await page.goto(`${STORE}${path}`);
  if (path.startsWith("/conta/enderecos")) {
    await expect(page.locator("[data-cep-ready]")).toBeAttached();
  }
}

test("o CEP preenche o endereço, e o que já foi escrito não é sobrescrito", async () => {
  await abrir("/conta/enderecos");

  await page.getByLabel("Nome de quem recebe").fill("Bia E2E");
  await page.getByLabel("Apelido").fill("centro");
  // A rua vai preenchida de propósito: quem está corrigindo o que o ViaCEP erra (condomínio,
  // loteamento novo) não pode ver o próprio texto sumir.
  await page.getByLabel("Rua").fill("Rua que eu escrevi");
  // Digitado de verdade, que é o que a pessoa faz. (O campo escuta o evento nativo, e não o
  // sintético do React, para o preenchimento automático do navegador também funcionar — mas
  // isso é comportamento de navegador, não algo que o Playwright saiba encenar.)
  await page.getByLabel("CEP").pressSequentially("01002020", { delay: 20 });

  await expect(page.getByText("Endereço preenchido")).toBeVisible();
  await expect(page.getByLabel("Cidade")).toHaveValue("São Paulo");
  await expect(page.getByLabel("Bairro")).toHaveValue("Centro");
  await expect(page.getByLabel("UF")).toHaveValue("SP");
  await expect(page.getByLabel("Rua")).toHaveValue("Rua que eu escrevi");

  await page.getByLabel("Número").fill("100");
  await page.getByRole("button", { name: "Salvar endereço" }).click();
  await expect(page.getByText("Endereço salvo.")).toBeVisible();
});

test("CEP que não existe não trava o formulário", async () => {
  await abrir("/conta/enderecos");
  await page.getByLabel("CEP").pressSequentially("99999999", { delay: 20 });
  await expect(page.getByText(/Não achamos esse CEP/)).toBeVisible();
  // O que importa: dá para seguir à mão.
  await expect(page.getByLabel("Cidade")).toBeEditable();
  await expect(page.getByRole("button", { name: "Salvar endereço" })).toBeEnabled();
});

test("editar um endereço tem saída: dá para voltar e cadastrar outro", async () => {
  // Era um beco sem saída: com `?editar=`, o formulário virava o de edição e não havia caminho
  // de volta para o de cadastro.
  await abrir("/conta/enderecos");
  await page.getByRole("link", { name: "Editar" }).first().click();
  await expect(page.getByRole("heading", { name: "Editar endereço" })).toBeVisible();

  await page.getByRole("link", { name: "Cancelar e cadastrar outro" }).click();
  await expect(page.getByRole("heading", { name: "Novo endereço" })).toBeVisible();
  await expect(page.getByLabel("Nome de quem recebe")).toHaveValue("");
});

test("salvar devolve a pessoa para onde ela veio", async () => {
  // Cadastrar endereço é um desvio, não um destino: quem saiu do carrinho (ou de qualquer
  // tela) para cadastrar precisa voltar sozinha, senão a compra morre na lista de endereços.
  // O destino aqui é `/conta` de propósito — provar o retorno não depende do carrinho, e
  // depender dele amarrava este teste ao estado que o checkout deixa.
  await abrir("/conta/enderecos?next=%2Fconta");

  await page.getByLabel("Nome de quem recebe").fill("Bia no trabalho");
  await page.getByLabel("Apelido").fill("trabalho");
  await page.getByLabel("CEP").pressSequentially("01310100", { delay: 20 });
  await expect(page.getByLabel("Cidade")).toHaveValue("São Paulo");
  await page.getByLabel("Número").fill("900");
  await page.getByRole("button", { name: "Salvar endereço" }).click();

  await expect(page).toHaveURL(/\/conta$|\/conta\?/);
});
