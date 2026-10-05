import { expect, type Page, test } from "@playwright/test";

import { PANEL } from "./playwright.config";

/**
 * WhatsApp do cliente obrigatório ou não: a loja escolhe em Entrega e checkout. O pedido em si
 * (número exigido, normalizado e guardado) é coberto no pytest; aqui, que a opção salva e volta.
 */
test.describe.configure({ mode: "serial" });

async function openStore(page: Page): Promise<string> {
  await page.goto(`${PANEL}/`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page.getByRole("heading", { name: /Olá, Admin E2E/ })).toBeVisible();
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  const tenantId = /\/t\/([0-9a-f-]{36})/.exec(page.url())?.[1];
  expect(tenantId).toBeTruthy();
  return tenantId as string;
}

async function salvar(page: Page, ligado: boolean): Promise<void> {
  const opcao = page.getByRole("checkbox", { name: /WhatsApp do cliente obrigatório no checkout/ });
  await opcao.setChecked(ligado);
  await page.getByRole("button", { name: "Salvar checkout" }).click();
  await expect(page).toHaveURL(/ok=checkout/);
  await expect(opcao).toBeChecked({ checked: ligado });
}

test("a loja liga e desliga o WhatsApp obrigatório no checkout", async ({ page }) => {
  const tenantId = await openStore(page);
  await page.goto(`${PANEL}/t/${tenantId}/entrega`);
  await expect(page.getByText(/Para você falar com o cliente sobre o pedido/)).toBeVisible();
  await salvar(page, true);
  // Volta ao padrão: os outros specs fecham pedido sem WhatsApp.
  await salvar(page, false);
});
