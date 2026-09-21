import { type Browser, expect, type Page, test } from "@playwright/test";

import { CLOSED_STORE, FAKE_GOOGLE, PANEL } from "./playwright.config";

// One shared seeded database: the flow runs in order (sign in → request → approve → catalog).
test.describe.configure({ mode: "serial" });

async function customerSignsIn(page: Page, identity: { sub: string; email: string; name: string }): Promise<void> {
  await page.request.post(`${FAKE_GOOGLE}/__e2e/identity`, { data: identity });
  await page.goto(`${CLOSED_STORE}/loja`);
  await expect(page).toHaveURL(/\/entrar\?next=%2Floja$/);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  // fake Google → api-commerce callback → store /auth/complete → access page
  await expect(page).toHaveURL(/\/acesso-pendente\?next=%2Floja$/);
}

async function adminPanel(browser: Browser): Promise<Page> {
  const page = await (await browser.newContext()).newPage();
  await page.goto(`${PANEL}/`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page.getByRole("heading", { name: /Olá, Admin E2E/ })).toBeVisible();
  await page.getByRole("link", { name: "Loja Fechada", exact: true }).click();
  await page.getByRole("link", { name: "Clientes" }).click();
  return page;
}

test("cliente entra com Google, pede acesso, a loja libera e o catálogo abre", async ({ page, browser, context }) => {
  await customerSignsIn(page, { sub: "e2e-ana", email: "ana.e2e@example.com", name: "Ana E2E" });

  const session = (await context.cookies(CLOSED_STORE)).find((c) => c.name === "__Host-mb_sess");
  expect(session?.httpOnly).toBe(true);
  expect(session?.sameSite).toBe("Lax");
  expect((await context.cookies(CLOSED_STORE)).some((c) => c.name === "__Host-mb_oidc")).toBe(false);

  await page.getByLabel("Mensagem para a loja (opcional)").fill("Sou cliente da feira");
  await page.getByRole("button", { name: "Solicitar acesso" }).click();
  await expect(page.getByText("Pedido enviado. A loja avisa quando liberar.")).toBeVisible();
  await expect(page.getByText("Seu pedido de acesso está em análise pela loja.")).toBeVisible();

  // The catalog stays closed while pending.
  await page.goto(`${CLOSED_STORE}/loja`);
  await expect(page).toHaveURL(/\/acesso-pendente/);

  const admin = await adminPanel(browser);
  const row = admin.getByRole("row", { name: /Ana E2E/ });
  await expect(row).toContainText("pendente");
  await expect(row).toContainText("Sou cliente da feira");
  await row.getByRole("button", { name: "Liberar" }).click();
  await expect(admin.getByText("Acesso liberado.")).toBeVisible();

  await page.goto(`${CLOSED_STORE}/loja`);
  await expect(page.getByText("Brownie de chocolate")).toBeVisible();

  await page.getByRole("button", { name: "Sair" }).click();
  await expect(page).toHaveURL(`${CLOSED_STORE}/`);
  await page.goto(`${CLOSED_STORE}/loja`);
  await expect(page).toHaveURL(/\/entrar\?next=%2Floja$/);
});

test("loja bloqueia um cliente e ele perde a sessão na hora", async ({ page, browser }) => {
  await customerSignsIn(page, { sub: "e2e-beto", email: "beto.e2e@example.com", name: "Beto E2E" });
  await page.getByRole("button", { name: "Solicitar acesso" }).click();
  await expect(page.getByText("Seu pedido de acesso está em análise pela loja.")).toBeVisible();

  const admin = await adminPanel(browser);
  const row = admin.getByRole("row", { name: /Beto E2E/ });
  await row.getByRole("textbox", { name: "Motivo" }).fill("Pedido de teste");
  await row.getByRole("button", { name: "Bloquear" }).click();
  await expect(admin.getByText("Cliente bloqueado nesta loja.")).toBeVisible();

  await page.goto(`${CLOSED_STORE}/loja`);
  await expect(page).toHaveURL(/\/entrar\?next=%2Floja&erro=sessao_expirada$|\/entrar\?next=%2Floja$/);
});

test("código de retorno forjado ou reusado volta para o login", async ({ page }) => {
  await page.goto(`${CLOSED_STORE}/auth/complete?hc=forjado-forjado-forjado`);
  await expect(page).toHaveURL(/\/entrar\?erro=sessao_expirada$/);
  await expect(page.getByRole("alert")).toHaveText("O login expirou. Entre de novo.");
});
