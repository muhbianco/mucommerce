import { expect, type Page, test } from "@playwright/test";

import { PANEL, STORE } from "./playwright.config";

/**
 * Serviços oferecidos (frete v2, §7.7): a loja escolhe, na tela de Envio, quais serviços da
 * conta na transportadora aparecem no checkout. A lista vem da própria transportadora (no E2E,
 * a fake: Econômico como os Correios, Expresso como a Jadlog, que pede nota fiscal).
 *
 * Deixa a loja como encontrou (os dois serviços marcados): os specs seguintes cotam com ela.
 */
test.describe.configure({ mode: "serial" });

async function abrirEnvio(page: Page): Promise<void> {
  await page.goto(`${PANEL}/`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page.getByRole("heading", { name: /Olá, Admin E2E/ })).toBeVisible();
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  await page.getByRole("link", { name: "Envio", exact: true }).first().click();
  await expect(page.getByRole("heading", { name: "Serviços oferecidos" })).toBeVisible();
}

function servico(page: Page, codigo: string) {
  return page.locator(`[data-service="${codigo}"]`).getByRole("checkbox");
}

test("a loja tira um serviço do checkout, e a vitrine para de mostrá-lo", async ({ page }) => {
  await abrirEnvio(page);
  await expect(page.getByText(/oferece todos os serviços da sua conta/)).toBeVisible();
  await expect(servico(page, "fake_economico")).toBeChecked();
  await expect(servico(page, "fake_expresso")).toBeChecked();
  const expresso = page.locator('[data-service="fake_expresso"]');
  await expect(expresso).toContainText("pede nota fiscal");
  await expect(expresso).toContainText("vários volumes numa etiqueta só");
  await expect(page.locator('[data-service="fake_economico"]')).toContainText("uma etiqueta por volume");

  await servico(page, "fake_expresso").uncheck();
  await page.getByRole("button", { name: "Salvar serviços" }).click();
  await expect(page.getByText("Serviços salvos")).toBeVisible();
  await expect(servico(page, "fake_expresso")).not.toBeChecked();
  await expect(page.getByText(/Serviço novo da transportadora só entra quando você marcar/)).toBeVisible();

  // Na vitrine, a estimativa por CEP já sai só com o que a loja oferece.
  await page.goto(`${STORE}/loja/produto/camiseta-muhbianco`);
  const frete = page.locator("#frete");
  await frete.getByLabel("CEP").fill("20040-020");
  await frete.getByRole("button", { name: "Calcular", exact: true }).click();
  const opcoes = frete.getByRole("list", { name: "Opções de frete" });
  await expect(opcoes.locator("li", { hasText: "Fake Econômico" })).toBeVisible();
  await expect(opcoes.locator("li", { hasText: "Fake Expresso" })).toHaveCount(0);
});

test("não dá para desmarcar todos; e a loja volta a oferecer os dois", async ({ page }) => {
  await abrirEnvio(page);
  await servico(page, "fake_economico").uncheck();
  await servico(page, "fake_expresso").uncheck();
  await page.getByRole("button", { name: "Salvar serviços" }).click();
  await expect(page.getByText(/Marque pelo menos um serviço/)).toBeVisible();

  await servico(page, "fake_economico").check();
  await servico(page, "fake_expresso").check();
  await page.getByRole("button", { name: "Salvar serviços" }).click();
  await expect(page.getByText("Serviços salvos")).toBeVisible();
  await expect(servico(page, "fake_economico")).toBeChecked();
  await expect(servico(page, "fake_expresso")).toBeChecked();
});
