import { expect, type Page, test } from "@playwright/test";

import { PANEL } from "./playwright.config";

// Tela de Envio em passos (docs/14-pedidos-lunares.md §4): a transportadora em uso, o token
// conferido antes de guardar e o último teste gravado.

async function envio(page: Page): Promise<void> {
  await page.goto(`${PANEL}/`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page.getByRole("heading", { name: /Olá, Admin E2E/ })).toBeVisible();
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  await page.getByRole("link", { name: "Envio", exact: true }).click();
  await expect(page.getByRole("heading", { name: "Passo a passo" })).toBeVisible();
}

test("envio: transportadora em uso, token recusado não conecta, teste fica gravado", async ({ page }) => {
  await envio(page);
  await expect(page.getByText("Transportadora de teste").first()).toBeVisible();
  await expect(page.getByText("em uso")).toBeVisible();

  await page.getByLabel(/^Token de acesso/).fill("token-recusado");
  await page.getByRole("button", { name: "Trocar token" }).click();
  await expect(page.getByText(/A transportadora recusou este token/)).toBeVisible();

  await page.getByRole("button", { name: "Testar agora" }).click();
  await expect(page.getByText(/Último teste em .*: funcionando/)).toBeVisible();
  await page.reload();
  await expect(page.getByText(/Último teste em .*: funcionando/)).toBeVisible();
});
