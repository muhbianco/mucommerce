import { expect, type Page, test } from "@playwright/test";

import { API, PANEL } from "./playwright.config";

/**
 * Entrega pela Uber, fase 1 (docs/15): a zona escolhe quem entrega, a coleta tem telefone e as
 * chaves da loja são conferidas na Uber antes de guardar. A Uber é a de mentira de
 * `api-commerce/tests/e2e/server.py` (`/__e2e/uber`). O telefone exigido no checkout e o resto da
 * regra estão no pytest (`tests/test_delivery_uber.py`); aqui, a tela.
 *
 * A entrega própria nasce desligada na loja semeada: o spec liga e desliga no fim.
 */
test.describe.configure({ mode: "serial" });

const ZONA = "Centro de São Paulo";
const SEGREDO = "segredo-da-loja-e2e";

async function openStore(page: Page): Promise<string> {
  await page.goto(`${PANEL}/`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page.getByRole("heading", { name: /Olá, Admin E2E/ })).toBeVisible();
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  const tenantId = /\/t\/([0-9a-f-]{36})/.exec(page.url())?.[1];
  expect(tenantId).toBeTruthy();
  return tenantId as string;
}

async function quemEntrega(page: Page, valor: "own" | "uber"): Promise<void> {
  const zona = page.locator("details", { has: page.locator("summary", { hasText: ZONA }) });
  await zona.locator("summary").click();
  await zona.getByLabel(/Quem entrega/).selectOption(valor);
  await page.getByRole("button", { name: "Salvar entrega" }).click();
  await expect(page).toHaveURL(/ok=entrega/);
}

async function conectar(page: Page, secret: string, customer = "cliente-da-loja-e2e"): Promise<void> {
  const uber = page.locator("#uber");
  await uber.getByLabel("Customer ID", { exact: true }).fill(customer);
  await uber.getByLabel("Client ID", { exact: true }).fill("client-da-loja-e2e");
  await uber.getByLabel("Client Secret", { exact: true }).fill(secret);
  await uber.getByRole("button", { name: /Conectar conta|Trocar chaves/ }).click();
}

test.beforeAll(async ({ request }) => {
  const ligado = await request.post(`${API}/__e2e/features`, { data: { flags: { delivery: true } } });
  expect(ligado.ok()).toBeTruthy();
});

test.afterAll(async ({ request }) => {
  await request.post(`${API}/__e2e/features`, { data: { flags: { delivery: false } } });
});

test("a loja conecta a Uber, marca a zona e informa a coleta", async ({ page }) => {
  const tenantId = await openStore(page);
  await page.goto(`${PANEL}/t/${tenantId}/entrega`);
  const uber = page.locator("#uber");
  await expect(uber.getByText("Desconectada", { exact: true })).toBeVisible();
  await expect(uber.getByText(/conectar a conta Uber Direct da loja/)).toBeVisible();

  // Chave recusada pela Uber não vira "conectada", e a tela diz qual conferir.
  await conectar(page, "segredo-recusado");
  await expect(page.getByText(/A Uber recusou o Client ID ou o Client Secret/)).toBeVisible();
  await expect(uber.getByText("Desconectada", { exact: true })).toBeVisible();
  await conectar(page, SEGREDO, "cliente-de-outra-conta");
  await expect(page.getByText(/o Customer ID não é da mesma conta/)).toBeVisible();

  await conectar(page, SEGREDO);
  await expect(page).toHaveURL(/ok=uber_conectada/);
  await expect(uber.getByText(/Conta conectada/)).toBeVisible();
  await expect(uber.getByText(/funcionando/)).toBeVisible();
  expect(await page.content()).not.toContain(SEGREDO);

  // Falta a zona e a coleta. A zona primeiro: salvar as zonas não pode apagar a conexão.
  await quemEntrega(page, "uber");
  await expect(page.locator("summary", { hasText: ZONA })).toContainText("entrega pela Uber");
  await expect(uber.getByText(/Conta conectada/)).toBeVisible();

  await uber.getByLabel("Nome na coleta").fill("Loja MuhBianco");
  await uber.getByLabel(/Telefone da coleta/).fill("(11) 98888-7777");
  await uber.getByLabel("CEP", { exact: true }).fill("01001-000");
  await uber.getByLabel("Rua", { exact: true }).fill("Rua E2E");
  await uber.getByLabel("Número", { exact: true }).fill("100");
  await uber.getByLabel("Bairro", { exact: true }).fill("Sé");
  await uber.getByLabel("Cidade", { exact: true }).fill("São Paulo");
  await uber.getByLabel("UF", { exact: true }).fill("sp");
  await uber.getByLabel(/Teto por corrida/).fill("25,00");
  await uber.getByRole("button", { name: "Salvar coleta e chamada" }).click();
  await expect(page).toHaveURL(/ok=uber_config/);
  await expect(uber.getByText("Pronta", { exact: true })).toBeVisible();
  await expect(uber.getByLabel(/Telefone da coleta/)).toHaveValue("+5511988887777");
  await expect(uber.getByLabel(/Teto por corrida/)).toHaveValue("25,00");

  await uber.getByRole("button", { name: "Testar agora" }).click();
  await expect(page).toHaveURL(/ok=uber_testada/);

  await uber.getByRole("button", { name: "Desconectar" }).click();
  await expect(page).toHaveURL(/ok=uber_desconectada/);
  await expect(uber.getByText("Desconectada", { exact: true })).toBeVisible();
  // A coleta ficou: só as chaves saíram.
  await expect(uber.getByLabel("Nome na coleta")).toHaveValue("Loja MuhBianco");

  // Volta ao semeado: os outros specs não contam com zona da Uber.
  await quemEntrega(page, "own");
});
