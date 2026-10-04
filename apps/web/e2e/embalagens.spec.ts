import { expect, type Page, test } from "@playwright/test";

import { PANEL } from "./playwright.config";

/**
 * Frete v2 (docs/13-frete-v2.md, F4/F5): a loja cadastra a embalagem padrão pelo tamanho
 * sugerido, acrescenta a Caixa P, mede a rabiola no produto e o simulador põe 4 rabiolas numa
 * Caixa P só — que é o caso que o motor antigo resolvia com duas caixas.
 */

async function signIn(page: Page): Promise<void> {
  await page.goto(`${PANEL}/`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page.getByRole("heading", { name: /Olá, Admin E2E/ })).toBeVisible();
}

async function openStore(page: Page): Promise<void> {
  await signIn(page);
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
}

async function createProduct(page: Page, name: string): Promise<void> {
  await page.getByRole("link", { name: "Produtos" }).first().click();
  const form = page.locator("form").filter({ has: page.getByRole("button", { name: /Criar/ }) });
  await form.locator('input[name="name"]').fill(name);
  await form.locator('input[name="price"]').fill("19,90");
  await form.getByRole("button", { name: /Criar/ }).click();
  await expect(page.getByText("Produto criado.")).toBeVisible();
}

test("embalagem padrão, Caixa P, rabiola medida e 4 rabiolas numa Caixa P", async ({ page }) => {
  await openStore(page);
  await page.getByRole("link", { name: "Embalagens" }).first().click();

  // Primeira vez: o assistente já vem com a Caixa M preenchida.
  await expect(page.getByRole("heading", { name: "Sua embalagem padrão" })).toBeVisible();
  await expect(page.locator('input[name="inner_length"]')).toHaveValue("30");
  await page.getByRole("button", { name: "Salvar embalagem padrão" }).click();
  await expect(page.getByText(/Embalagem cadastrada/)).toBeVisible();
  await expect(page.getByText(/Para a transportadora/)).toBeVisible();

  await page.getByRole("link", { name: "Voltar" }).click();
  await page.getByRole("button", { name: "Adicionar Caixa P" }).click();
  await expect(page.getByText(/Embalagem cadastrada/)).toBeVisible();

  // Produto: peso e medidas em g e cm, e "onde cabe" vindo do servidor.
  await createProduct(page, "Rabiola E2E");
  await page.locator('input[name="weight_value"]').fill("150");
  await page.locator('input[name="depth_cm"]').fill("10");
  await page.locator('input[name="width_cm"]').fill("10");
  await page.locator('input[name="height_cm"]').fill("5");
  await page.getByRole("button", { name: /Salvar produto/ }).first().click();
  await expect(page.getByText("Alterações salvas.")).toBeVisible();
  const ondeCabe = page.locator("h5", { hasText: "Onde cabe" }).locator("..");
  await expect(ondeCabe.getByText("Caixa P")).toBeVisible();
  await expect(ondeCabe.locator("li", { hasText: "Caixa P" })).toContainText("até 6 unidades");
  await expect(ondeCabe.locator("li", { hasText: "Caixa M" })).toContainText("até 18 unidades");

  // Simulador: 4 rabiolas cabem numa Caixa P só.
  await page.getByRole("link", { name: "Testar frete deste produto" }).click();
  await page.locator('input[name="q0"]').fill("4");
  await page.getByRole("button", { name: "Montar as caixas" }).click();
  const consolidado = page.locator('[data-strategy="consolidate"]');
  await expect(consolidado).toContainText("Menos volumes");
  await expect(consolidado.locator("ol > li")).toHaveCount(1);
  await expect(consolidado).toContainText("Caixa P");
  await expect(consolidado).toContainText("4× Rabiola E2E");
});

test("capacidade declarada: rígido só limita, flexível declara até o dobro, 50 é recusado", async ({ page }) => {
  await openStore(page);
  await createProduct(page, "Rabiola Declarada");
  await page.locator('input[name="weight_value"]').fill("150");
  await page.locator('input[name="depth_cm"]').fill("10");
  await page.locator('input[name="width_cm"]').fill("10");
  await page.locator('input[name="height_cm"]').fill("5");
  await page.locator('input[name="packing_mode"][value="restricted"]').check();
  const caixaM = page.locator("div", { has: page.getByText("Caixa M", { exact: true }) }).filter({
    has: page.locator('input[name="rule_pkg"]'),
  });
  await caixaM.locator('input[name="rule_pkg"]').first().check();
  await caixaM.locator('input[name^="rule_max_"]').first().fill("25");
  await page.getByRole("button", { name: /Salvar produto/ }).first().click();
  const linhaM = page.locator("h5", { hasText: "Onde cabe" }).locator("..").locator("li", { hasText: "Caixa M" });
  await expect(linhaM).toContainText("até 18 unidades");
  await expect(linhaM).toContainText("limite seu: 25");

  // Flexível: a declaração vale (139%: passa, com aviso).
  await page.getByText(/Características/).click();
  await page.locator('input[name="packing_flexible"]').check();
  await page.getByRole("button", { name: /Salvar produto/ }).first().click();
  await expect(linhaM).toContainText("18 calculadas");
  await expect(linhaM).toContainText("25 declaradas");
  await expect(linhaM).toContainText(/acima do espaço físico/);

  // 50 faria o produto encolher para menos da metade: recusado com a frase da regra.
  await page.locator('input[name^="rule_max_"]').first().fill("50");
  await page.getByRole("button", { name: /Salvar produto/ }).first().click();
  await expect(page.getByText(/encolher para menos da metade/).first()).toBeVisible();
});
