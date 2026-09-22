import { expect, test } from "@playwright/test";

import { API, FAKE_GOOGLE, FAKE_N8N, STORE } from "./playwright.config";

// One shared seeded database: the purchase runs in order (cart → checkout → payment).
test.describe.configure({ mode: "serial" });

const BIA = { sub: "e2e-bia", email: "bia.e2e@example.com", name: "Bia E2E" };

test("carrinho: entra para comprar, escolhe tamanho e adicional, retira na loja", async ({ page }) => {
  await page.request.post(`${FAKE_GOOGLE}/__e2e/identity`, { data: BIA });
  const product = `${STORE}/loja/produto/camiseta-muhbianco`;
  const buy = page.getByRole("button", { name: "Adicionar ao carrinho" });
  await page.goto(product);
  await expect(buy).toBeEnabled(); // enabled once the picker is live (hydrated)
  await page.getByRole("radio", { name: "G" }).check();
  await buy.click();

  // Not signed in yet: the store sends the customer to sign in and back to the product.
  await expect(page).toHaveURL(/\/entrar\?next=%2Floja%2Fproduto%2Fcamiseta-muhbianco$/);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page).toHaveURL(product);

  // The status line follows the component state: once it shows the new price, the form holds it.
  const status = page.getByRole("status");
  await expect(buy).toBeEnabled();
  await page.getByRole("radio", { name: "G" }).check();
  await expect(status).toContainText(/R\$\s*69,00/);
  await page.getByRole("checkbox", { name: /Personalizada/ }).check();
  await expect(status).toContainText(/R\$\s*79,00/);
  await buy.click();
  await expect(page).toHaveURL(/\/carrinho\?ok=adicionado$/);
  const line = page.getByRole("row", { name: /Camiseta MuhBianco — G/ });
  await expect(line).toContainText("Personalizada");
  await expect(line).toContainText(/R\$\s*79,00/);
  await expect(page.getByText("Escolha como receber para continuar.")).toBeVisible();

  await page.getByLabel(/Retirar em Loja MuhBianco/).check();
  await page.getByRole("button", { name: "Usar esta opção" }).click();
  await expect(page.getByText(/Total: R\$\s*79,00/)).toBeVisible();
  await expect(page.getByRole("link", { name: "Finalizar compra" })).toBeVisible();

  // A paused size cannot be added (the button is off for it).
  await page.goto(product);
  await expect(buy).toBeEnabled();
  await page.getByRole("radio", { name: "M" }).check();
  await expect(page.getByRole("status")).toContainText("Indisponível");
  await expect(buy).toBeDisabled();
});

test("checkout: revisa, aceita os termos quando houver e faz o pedido", async ({ page }) => {
  // A new browser context per test: sign in again as the same customer (the cart is theirs).
  await page.request.post(`${FAKE_GOOGLE}/__e2e/identity`, { data: BIA });
  await page.goto(`${STORE}/carrinho`);
  await expect(page).toHaveURL(/\/entrar\?next=%2Fcarrinho$/);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page).toHaveURL(`${STORE}/carrinho`);
  await page.getByRole("link", { name: "Finalizar compra" }).click();
  await expect(page).toHaveURL(`${STORE}/checkout`);
  await expect(page.getByText(/Retirada em Loja MuhBianco/)).toBeVisible();
  await expect(page.getByText(/Total: R\$\s*79,00/)).toBeVisible();
  const accept = page.getByRole("checkbox", { name: /Li e aceito/ });
  if (await accept.count()) await accept.check();
  await page.getByRole("button", { name: "Fazer pedido" }).click();

  await expect(page).toHaveURL(/\/conta\/pedidos\/[0-9a-f-]{36}\?ok=pedido$/);
  await expect(page.getByRole("heading", { name: /Pedido #\d+/ })).toBeVisible();
  await expect(page.getByText("Pedido recebido!")).toBeVisible();
  await expect(page.getByText(/Aguardando pagamento — pague até/)).toBeVisible();
  await expect(page.getByText(/Total: R\$\s*79,00/)).toBeVisible();

  // The cart became the order: a new, empty cart.
  await page.goto(`${STORE}/carrinho`);
  await expect(page.getByText("Seu carrinho está vazio.")).toBeVisible();
});

test("meus pedidos: lista e cancela o pedido que aguarda pagamento", async ({ page }) => {
  await page.request.post(`${FAKE_GOOGLE}/__e2e/identity`, { data: BIA });
  await page.goto(`${STORE}/conta/pedidos`);
  await expect(page).toHaveURL(/\/entrar\?next=%2Fconta%2Fpedidos$/);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page).toHaveURL(`${STORE}/conta/pedidos`);

  const row = page.getByRole("row", { name: /Pedido #\d+/ }).first();
  await expect(row).toContainText("Aguardando pagamento");
  await row.getByRole("link").click();
  await page.getByRole("button", { name: "Cancelar pedido" }).click();
  await expect(page.getByText("Pedido cancelado.")).toBeVisible();
  await expect(page.getByText("Cancelado", { exact: true }).first()).toBeVisible();
  await expect(page.getByRole("button", { name: "Cancelar pedido" })).toHaveCount(0);
});

test("pagamento: paga com Pix e a página confirma sozinha", async ({ page }) => {
  await page.request.post(`${FAKE_GOOGLE}/__e2e/identity`, { data: BIA });
  const product = `${STORE}/loja/produto/camiseta-muhbianco`;
  await page.goto(`${STORE}/entrar?next=${encodeURIComponent("/loja/produto/camiseta-muhbianco")}`);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  await expect(page).toHaveURL(product);
  const buy = page.getByRole("button", { name: "Adicionar ao carrinho" });
  await expect(buy).toBeEnabled();
  await page.getByRole("radio", { name: "P" }).check();
  await expect(page.getByRole("status")).toContainText(/R\$\s*59,00/);
  await buy.click();
  await expect(page).toHaveURL(/\/carrinho\?ok=adicionado$/);
  await page.getByLabel(/Retirar em Loja MuhBianco/).check();
  await page.getByRole("button", { name: "Usar esta opção" }).click();

  // A coupon takes 10% off the goods (never off the delivery).
  await page.getByLabel("Cupom de desconto").fill("e2e10");
  await page.getByRole("button", { name: "Aplicar cupom" }).click();
  await expect(page.getByText("Cupom E2E10 aplicado.")).toBeVisible();
  await expect(page.getByText(/Desconto:\s*−?R\$\s*5,90/)).toBeVisible();
  await expect(page.getByText(/Total:\s*R\$\s*53,10/)).toBeVisible();

  await page.getByRole("link", { name: "Finalizar compra" }).click();
  const accept = page.getByRole("checkbox", { name: /Li e aceito/ });
  if (await accept.count()) await accept.check();
  await page.getByRole("button", { name: "Fazer pedido" }).click();
  await expect(page).toHaveURL(/\/conta\/pedidos\/[0-9a-f-]{36}\?ok=pedido$/);

  await page.getByRole("button", { name: "Pagar com Pix" }).click();
  await expect(page).toHaveURL(/\?ok=pagamento$/);
  await expect(page.getByText("Aguardando o seu pagamento")).toBeVisible();
  await expect(page.getByRole("img", { name: "QR Code do Pix" })).toBeVisible();
  await expect(page.getByLabel("Pix copia e cola")).toHaveValue(/^00020126fake/);
  await expect(page.getByRole("button", { name: "Pagar com Pix" })).toHaveCount(0); // one at a time

  // The bank pays; the provider notifies the store; the page notices by itself (no reload).
  const settled = await page.request.post(`${API}/__e2e/payments/settle`, { data: { tenant: "muhbianco" } });
  expect((await settled.json()).webhook).toBe(200);
  await expect(page.getByText("Pagamento aprovado")).toBeVisible({ timeout: 20_000 });
  await expect(page.getByText("Pagamento confirmado", { exact: true }).first()).toBeVisible();
  await expect(page.getByLabel("Pix copia e cola")).toHaveCount(0);

  // The store writes to the customer: the beat runs the outbox and the e-mails (n8n).
  await page.request.post(`${API}/__e2e/tick`, { data: {} });
  const inbox = await page.request.get(`${FAKE_N8N}/__e2e/messages?to=${BIA.email}`);
  const subjects = (await inbox.json()).map((m: { subject: string }) => m.subject);
  expect(subjects.some((s: string) => s.startsWith("Pagamento confirmado"))).toBe(true);
});
