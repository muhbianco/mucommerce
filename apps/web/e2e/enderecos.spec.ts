import { expect, type Page, test } from "@playwright/test";

import { FAKE_GOOGLE, STORE } from "./playwright.config";

// Roda depois de customers.spec (um banco semeado só, ordem alfabética): a Bia já entrou e foi
// liberada pela loja lá.
test.describe.configure({ mode: "serial" });

const BIA = { sub: "e2e-bia", email: "bia.e2e@example.com", name: "Bia E2E" };

/** A busca de CEP só vale depois que a ilha está de pé; digitar antes é digitar no vazio. */
async function esperarCampoVivo(page: Page): Promise<void> {
  await expect(page.locator("[data-cep-ready]")).toBeAttached();
}

async function entrar(page: Page, request: import("@playwright/test").APIRequestContext, next: string) {
  page.on("console", (m) => console.log(`[c ${m.type()}]`, m.text().slice(0, 200)));
  page.on("pageerror", (e) => console.log("[pageerror]", e.message.slice(0, 250)));
  page.on("response", (r) => {
    if (r.status() >= 400) console.log("[http]", r.status(), r.url().slice(0, 140));
  });
  await request.post(`${FAKE_GOOGLE}/__e2e/identity`, { data: BIA });
  await page.goto(`${STORE}/entrar?next=${encodeURIComponent(next)}`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page).toHaveURL(new RegExp(next.split("?")[0].replace(/\//g, "\/")));
  if (next.startsWith("/conta/enderecos")) await esperarCampoVivo(page);
}

test("o CEP preenche o endereço, e o que já foi escrito não é sobrescrito", async ({
  page,
  request,
}) => {
  await entrar(page, request, "/conta/enderecos");

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

test("CEP que não existe não trava o formulário", async ({ page, request }) => {
  await entrar(page, request, "/conta/enderecos");
  await page.getByLabel("CEP").pressSequentially("99999999", { delay: 20 });
  await expect(page.getByText(/Não achamos esse CEP/)).toBeVisible();
  // O que importa: dá para seguir à mão.
  await expect(page.getByLabel("Cidade")).toBeEditable();
  await expect(page.getByRole("button", { name: "Salvar endereço" })).toBeEnabled();
});

test("editar um endereço tem saída: dá para voltar e cadastrar outro", async ({ page, request }) => {
  // Era um beco sem saída: com `?editar=`, o formulário virava o de edição e não havia caminho
  // de volta para o de cadastro.
  await entrar(page, request, "/conta/enderecos");
  await page.getByRole("link", { name: "Editar" }).first().click();
  await expect(page.getByRole("heading", { name: "Editar endereço" })).toBeVisible();

  await page.getByRole("link", { name: "Cancelar e cadastrar outro" }).click();
  await expect(page.getByRole("heading", { name: "Novo endereço" })).toBeVisible();
  await expect(page.getByLabel("Nome de quem recebe")).toHaveValue("");
});

test("cadastrar endereço a partir do carrinho devolve a pessoa para a compra", async ({
  page,
  request,
}) => {
  // O endereço do carrinho é um desvio, não um destino: quem saiu para cadastrar precisa voltar
  // sozinha para onde estava, senão a compra morre na lista de endereços.
  await entrar(page, request, "/conta/enderecos?next=%2Fcarrinho");

  await page.getByLabel("Nome de quem recebe").fill("Bia no trabalho");
  await page.getByLabel("Apelido").fill("trabalho");
  await page.getByLabel("CEP").pressSequentially("01310100", { delay: 20 });
  await expect(page.getByLabel("Cidade")).toHaveValue("São Paulo");
  await page.getByLabel("Número").fill("900");
  await page.getByRole("button", { name: "Salvar endereço" }).click();

  await expect(page).toHaveURL(/\/carrinho/);
});
