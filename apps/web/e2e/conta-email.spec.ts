import { expect, type Page, test } from "@playwright/test";

import { API, FAKE_N8N, STORE } from "./playwright.config";

// Conta com e-mail e senha (docs/14-pedidos-lunares.md §2): cadastro → link → entra; sai, entra
// com a senha; esquece, troca pelo link e entra com a nova.
test.describe.configure({ mode: "serial" });

const LIA = { name: "Lia E2E", email: "lia.e2e@example.com", password: "lua-cheia-2026" };

/** O link do último e-mail daquele modelo para `to`, já apontando para a loja do teste. */
async function linkFrom(page: Page, to: string, template: string, path: string): Promise<string> {
  await page.request.post(`${API}/__e2e/tick`, { data: {} });
  const inbox = (await (await page.request.get(`${FAKE_N8N}/__e2e/messages?to=${to}`)).json()) as {
    template: string;
    text: string;
  }[];
  const mail = inbox.find((m) => m.template === template);
  expect(mail, `e-mail ${template} para ${to}`).toBeTruthy();
  const k = /[?]k=([\w.-]+)/.exec(mail!.text)?.[1];
  expect(k).toBeTruthy();
  return `${STORE}${path}?k=${k}`;
}

test("cria a conta com e-mail, confirma pelo link e já entra", async ({ page, context }) => {
  await page.goto(`${STORE}/entrar`);
  await page.getByRole("link", { name: "Criar conta com e-mail" }).click();
  await page.getByLabel("Nome").fill(LIA.name);
  await page.getByLabel("E-mail").fill(LIA.email);
  await page.getByLabel(/^Senha/).fill(LIA.password);
  await page.getByLabel("Repita a senha").fill(LIA.password);
  await page.getByRole("button", { name: "Criar conta" }).click();
  await expect(page.getByText(/Enviamos um link para o seu e-mail/)).toBeVisible();
  expect((await context.cookies(STORE)).some((c) => c.name === "__Host-mb_sess")).toBe(false);

  await page.goto(await linkFrom(page, LIA.email, "account_confirm", "/entrar/confirmar"));
  await page.getByRole("button", { name: "Confirmar e entrar" }).click();
  await expect(page).toHaveURL(/\/loja$/);
  expect((await context.cookies(STORE)).some((c) => c.name === "__Host-mb_sess")).toBe(true);
});

test("sai e entra de novo com a senha; senha errada não entra", async ({ page }) => {
  await page.goto(`${STORE}/entrar`);
  const form = page.getByRole("form", { name: "Entrar com e-mail" });
  await form.getByLabel("E-mail").fill(LIA.email);
  await form.getByLabel("Senha").fill("nao-e-essa-senha");
  await form.getByRole("button", { name: "Entrar com e-mail" }).click();
  await expect(page.getByText("E-mail ou senha incorretos.")).toBeVisible();

  await form.getByLabel("E-mail").fill(LIA.email);
  await form.getByLabel("Senha").fill(LIA.password);
  await form.getByRole("button", { name: "Entrar com e-mail" }).click();
  await expect(page).toHaveURL(/\/loja$/);
});

test("esqueceu a senha: troca pelo link e entra com a nova", async ({ page }) => {
  await page.goto(`${STORE}/entrar`);
  await page.getByRole("link", { name: "Esqueci a senha" }).click();
  await page.getByLabel("E-mail da conta").fill(LIA.email);
  await page.getByRole("button", { name: "Enviar link" }).click();
  await expect(page.getByText(/enviamos um link para trocar/)).toBeVisible();

  await page.goto(await linkFrom(page, LIA.email, "password_reset", "/entrar/nova-senha"));
  await page.getByLabel("Senha nova").fill("nova-lua-2027");
  await page.getByLabel("Repita a senha").fill("nova-lua-2027");
  await page.getByRole("button", { name: "Trocar e entrar" }).click();
  await expect(page).toHaveURL(/\/$|\/loja$/);

  await page.goto(`${STORE}/entrar`);
  const form = page.getByRole("form", { name: "Entrar com e-mail" });
  await form.getByLabel("E-mail").fill(LIA.email);
  await form.getByLabel("Senha").fill(LIA.password);
  await form.getByRole("button", { name: "Entrar com e-mail" }).click();
  await expect(page.getByText("E-mail ou senha incorretos.")).toBeVisible();
});
