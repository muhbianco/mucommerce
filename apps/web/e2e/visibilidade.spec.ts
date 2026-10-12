import { type Browser, expect, type Page, test } from "@playwright/test";

import { CLOSED_STORE, PANEL } from "./playwright.config";

// Visibilidade da loja fechada (docs/14-pedidos-lunares.md §3): senha da loja, produto exposto na
// home e "toda fechada". Mexe na Loja Fechada do seed e devolve tudo como estava no fim.
test.describe.configure({ mode: "serial" });

const SENHA = "lua2026";

async function adminOfClosedStore(browser: Browser): Promise<Page> {
  const page = await (await browser.newContext()).newPage();
  await page.goto(`${PANEL}/`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page.getByRole("heading", { name: /Olá, Admin E2E/ })).toBeVisible();
  await page.getByRole("link", { name: "Loja Fechada", exact: true }).click();
  return page;
}

async function openModules(admin: Page): Promise<void> {
  await admin.getByRole("link", { name: "Módulos" }).first().click();
  await expect(admin.getByRole("heading", { name: "Quem vê a sua vitrine" })).toBeVisible();
}

async function setExposure(admin: Page, label: string): Promise<void> {
  await admin.getByRole("link", { name: "Produtos" }).first().click();
  await admin.getByRole("link", { name: "Brownie de chocolate" }).click();
  await admin.getByLabel("Na página inicial da loja fechada").selectOption({ label });
  await admin.getByRole("button", { name: "Salvar produto" }).click();
  await expect(admin.getByText("Alterações salvas.")).toBeVisible();
}

async function visitor(browser: Browser): Promise<Page> {
  return (await browser.newContext()).newPage();
}

test("a senha da loja abre a vitrine; senha errada fica na porta", async ({ browser }) => {
  const admin = await adminOfClosedStore(browser);
  await openModules(admin);
  await admin.getByLabel(/^Senha/).fill(SENHA);
  await admin.getByRole("button", { name: "Definir senha" }).click();
  await expect(admin.getByText(/Senha da loja salva/)).toBeVisible();

  const page = await visitor(browser);
  await page.goto(`${CLOSED_STORE}/loja`);
  await expect(page).toHaveURL(/\/entrar\?next=%2Floja$/);
  const door = page.getByRole("form", { name: "Senha da loja" });
  await door.getByLabel("Tenho a senha da loja").fill("errada");
  await door.getByRole("button", { name: "Entrar na loja" }).click();
  await expect(page.getByText("Senha da loja incorreta.")).toBeVisible();
  await door.getByLabel("Tenho a senha da loja").fill(SENHA);
  await door.getByRole("button", { name: "Entrar na loja" }).click();
  await expect(page).toHaveURL(/\/loja$/);
  await expect(page.getByText("Brownie de chocolate").first()).toBeVisible();
});

test("produto exposto aparece na home de quem ainda não entrou", async ({ browser }) => {
  const admin = await adminOfClosedStore(browser);
  await setExposure(admin, "Aparece; comprar pede a senha ou aprovação");

  const page = await visitor(browser);
  await page.goto(`${CLOSED_STORE}/`);
  await expect(page.getByText("Brownie de chocolate").first()).toBeVisible();
  await page.getByText("Brownie de chocolate").first().click();
  await expect(page).toHaveURL(/\/loja\/produto\//);
  await expect(page.getByRole("heading", { name: "Brownie de chocolate" })).toBeVisible();
  // O resto da vitrine continua fechado.
  await page.goto(`${CLOSED_STORE}/loja`);
  await expect(page).toHaveURL(/\/entrar/);
});

test("toda fechada: nem a página inicial aparece antes de entrar", async ({ browser }) => {
  const admin = await adminOfClosedStore(browser);
  await openModules(admin);
  await admin.getByLabel("Toda fechada").check();
  await admin.getByRole("button", { name: "Salvar", exact: true }).click();
  await expect(admin.getByText("Quem vê a vitrine foi atualizado.")).toBeVisible();

  const page = await visitor(browser);
  await page.goto(`${CLOSED_STORE}/`);
  await expect(page).toHaveURL(/\/entrar/);

  // Devolve a Loja Fechada como o seed a deixou: home aberta, sem senha, produto escondido.
  // O aviso "atualizado" já está na tela desde o salvamento anterior: espera a resposta do
  // POST e recarrega, senão o próximo clique cai no meio da navegação.
  await admin.getByLabel("Fechada, com a página inicial aberta").check();
  await Promise.all([
    admin.waitForResponse((r) => r.request().method() === "POST"),
    admin.getByRole("button", { name: "Salvar", exact: true }).click(),
  ]);
  await admin.goto(admin.url().split("?")[0] ?? admin.url());
  await expect(admin.getByLabel("Fechada, com a página inicial aberta")).toBeChecked();
  await admin.getByRole("button", { name: "Tirar a senha" }).click();
  await expect(admin.getByText(/A loja não tem mais senha/)).toBeVisible();
  await setExposure(admin, "Não aparece (só para quem entrou)");
});
