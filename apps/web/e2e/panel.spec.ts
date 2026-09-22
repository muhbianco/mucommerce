import { expect, type Page, test } from "@playwright/test";

import { PANEL, STORE } from "./playwright.config";

async function signIn(page: Page): Promise<void> {
  await page.goto(`${PANEL}/`);
  await expect(page).toHaveURL(/\/entrar/);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  // fake-accounts → /sso/callback → continue page → home
  await expect(page.getByRole("heading", { name: /Olá, Admin E2E/ })).toBeVisible();
}

test("login com a conta MuhBianco, lojas visíveis para admin da plataforma", async ({ page, context }) => {
  await signIn(page);
  await expect(page.getByRole("link", { name: "MuhBianco", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Loja Fechada", exact: true })).toBeVisible();

  const cookies = await context.cookies(PANEL);
  const session = cookies.filter((cookie) => cookie.name.startsWith("__Host-"));
  expect(session.map((cookie) => cookie.name).sort()).toEqual(["__Host-mb_at", "__Host-mb_rt"]);
  for (const cookie of session) {
    expect(cookie.httpOnly).toBe(true);
    expect(cookie.sameSite).toBe("Strict");
  }
});

test("código de login falso volta para a tela de entrar", async ({ page }) => {
  await page.goto(`${PANEL}/sso/callback?code=forjado&state=qualquer`);
  await expect(page).toHaveURL(/\/entrar\?erro=sso/);
});

test("criar produto; publicar sem imagem é recusado; vitrine não mostra", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  await page.getByRole("link", { name: "Produtos" }).first().click();

  const form = page.locator("form").filter({ has: page.getByRole("button", { name: /Criar/ }) });
  await form.locator('input[name="name"]').fill("Torta E2E");
  await form.locator('input[name="price"]').fill("25,00");
  await form.getByRole("button", { name: /Criar/ }).click();

  await expect(page.getByText("Produto criado.")).toBeVisible();
  await expect(page.locator('input[name="name"]').first()).toHaveValue("Torta E2E");

  await page.getByRole("button", { name: "Publicar" }).click();
  await expect(page.getByText(/produto sem imagem/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Publicar" })).toBeVisible();

  const shop = await page.goto(`${STORE}/loja/produto/torta-e2e`);
  expect(shop?.status()).toBe(404);
});

test("pausar a venda deixa o produto indisponível na vitrine; opções geram variantes", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  await page.getByRole("link", { name: "Produtos" }).first().click();
  await page.getByRole("link", { name: "Bolo de cenoura" }).click();
  const productUrl = page.url().split("?")[0] ?? "";

  await page.getByLabel("Motivo (só você vê)").fill("forno em manutenção");
  await page.getByRole("button", { name: "Pausar venda" }).click();
  await expect(page.getByText(/Venda pausada: o produto segue/)).toBeVisible();
  await expect(page.getByText(/forno em manutenção/)).toBeVisible();

  await page.goto(`${STORE}/loja/produto/bolo-de-cenoura`);
  await expect(page.getByText("Indisponível").first()).toBeVisible();

  await page.goto(productUrl);
  await page.getByRole("button", { name: "Retomar venda" }).click();
  await expect(page.getByText("Venda retomada.")).toBeVisible();

  await page.getByLabel("Opção 1").fill("Tamanho");
  await page.getByLabel("Valores (separados por vírgula)").first().fill("Pequeno, Grande");
  await page.getByRole("button", { name: "Salvar opções" }).click();
  await expect(page.getByText("Opções salvas; variantes atualizadas.")).toBeVisible();
  await expect(page.getByText(/^Pequeno · P\d+$/)).toBeVisible();
  await expect(page.getByText(/^Grande · P\d+-1$/)).toBeVisible();
});
