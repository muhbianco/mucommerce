import { expect, type Page, test } from "@playwright/test";

import { API, FAKE_GOOGLE, FAKE_N8N, PANEL, STORE } from "./playwright.config";

// Runs after checkout.spec (one seeded database, alphabetical order): the order paid with Pix
// there is the one the store now handles. The panel user is the model store's owner.
test.describe.configure({ mode: "serial" });

const BIA = { sub: "e2e-bia", email: "bia.e2e@example.com", name: "Bia E2E" };

async function signInToPanel(page: Page): Promise<void> {
  await page.goto(`${PANEL}/`);
  await expect(page).toHaveURL(/\/entrar/);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page.getByRole("heading", { name: /Olá, Admin E2E/ })).toBeVisible();
}

test("painel: a loja acompanha o pedido pago e marca como pronto", async ({ page }) => {
  await signInToPanel(page);
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  await page.getByRole("link", { name: "Pedidos" }).click();

  const row = page.getByRole("row", { name: /Bia E2E/ }).first();
  await expect(row).toContainText("Pago");
  await row.getByRole("link").click();

  await expect(page.getByRole("heading", { name: /Pedido #\d+ — Pago/ })).toBeVisible();
  await expect(page.getByText(/retirada em Loja MuhBianco/i)).toBeVisible();
  await expect(page.getByText(/fake · pix/)).toBeVisible();
  await expect(page.getByText(/Aprovado/)).toBeVisible();
  // The e-mails the store sent for this order are listed with their status.
  await expect(page.getByText(/Pagamento confirmado/).first()).toBeVisible();

  await page.getByRole("button", { name: "Aceitar pedido" }).click();
  await expect(page.getByText("Pedido atualizado.")).toBeVisible();
  await page.getByRole("button", { name: "Pronto para retirar" }).click();
  await expect(page.getByRole("heading", { name: /Pronto para retirar/ })).toBeVisible();
});

test("cliente: vê o pedido pronto para retirar e o aviso por e-mail", async ({ page }) => {
  await page.request.post(`${FAKE_GOOGLE}/__e2e/identity`, { data: BIA });
  await page.goto(`${STORE}/conta/pedidos`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page).toHaveURL(`${STORE}/conta/pedidos`);

  const row = page.getByRole("row", { name: /Pedido #\d+/ }).first();
  await expect(row).toContainText("Pronto para retirar");
  await row.getByRole("link").click();
  await expect(page.getByText("Pagamento aprovado")).toBeVisible();

  // The status change is told to the customer by e-mail (the beat sends it).
  await page.request.post(`${API}/__e2e/tick`, { data: {} });
  const inbox = await page.request.get(`${FAKE_N8N}/__e2e/messages?to=${BIA.email}`);
  const subjects = (await inbox.json()).map((m: { subject: string }) => m.subject);
  expect(subjects.some((s: string) => s.includes("pronto para retirar"))).toBe(true);
});

test("painel: cancelar um pedido pago devolve o dinheiro e o estoque", async ({ page }) => {
  // A fresh order, paid and then cancelled by the store.
  await page.request.post(`${FAKE_GOOGLE}/__e2e/identity`, { data: BIA });
  const product = `${STORE}/loja/produto/camiseta-muhbianco`;
  await page.goto(`${STORE}/entrar?next=${encodeURIComponent("/loja/produto/camiseta-muhbianco")}`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page).toHaveURL(product);
  const buy = page.getByRole("button", { name: "Adicionar ao carrinho" });
  await expect(buy).toBeEnabled();
  await page.getByRole("radio", { name: "G" }).check();
  await buy.click();
  await page.getByLabel(/Retirar em Loja MuhBianco/).check();
  await page.getByRole("button", { name: "Usar esta opção" }).click();
  await page.getByRole("link", { name: "Finalizar compra" }).click();
  const accept = page.getByRole("checkbox", { name: /Li e aceito/ });
  if (await accept.count()) await accept.check();
  await page.getByRole("button", { name: "Fazer pedido" }).click();
  await page.getByRole("button", { name: "Pagar com Pix" }).click();
  await expect(page.getByLabel("Pix copia e cola")).toBeVisible(); // the payment exists now
  const settled = await page.request.post(`${API}/__e2e/payments/settle`, {
    data: { tenant: "muhbianco" },
  });
  expect((await settled.json()).webhook).toBe(200);
  await expect(page.getByText("Pagamento aprovado")).toBeVisible({ timeout: 20_000 });

  await signInToPanel(page);
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  await page.getByRole("link", { name: "Pedidos" }).click();
  await page.getByRole("row", { name: /Bia E2E/ }).first().getByRole("link").click();
  await page.getByRole("textbox", { name: "Motivo do cancelamento" }).fill("sem estoque");
  await page.getByRole("button", { name: /Cancelar pedido e devolver o dinheiro/ }).click();

  await expect(page.getByRole("heading", { name: /— Cancelado/ })).toBeVisible();
  await expect(page.getByText(/Concluída · decisão da loja/)).toBeVisible();
  await expect(page.getByText("com devolução")).toBeVisible();
});
