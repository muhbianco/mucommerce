import { expect, type Page, test } from "@playwright/test";

import { API, PANEL } from "./playwright.config";

/**
 * Frete v2, F7 (docs/13-frete-v2.md): no pedido, o "Como embalar" sai do plano de volumes
 * congelado, imprime sozinho, e com uma etiqueta por volume cada volume tem a sua.
 *
 * A suíte não tem transportadora: `/__e2e/orders/shipping-sample` copia o último pedido da loja
 * (o de checkout.spec) em dois pedidos de transportadora com plano — um aceito e um já
 * despachado. A compra das etiquetas em si é coberta no pytest.
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

async function samples(page: Page): Promise<{ accepted: string; shipped: string }> {
  const resposta = await page.request.post(`${API}/__e2e/orders/shipping-sample`, { data: {} });
  expect(resposta.ok()).toBe(true);
  return resposta.json();
}

test("pedido aceito: Como embalar com os volumes do plano, e só ele vai para o papel", async ({ page }) => {
  const ids = await samples(page);
  const tenantId = await openStore(page);
  await page.goto(`${PANEL}/t/${tenantId}/pedidos/${ids.accepted}`);

  const lista = page.locator("#como-embalar");
  await expect(lista.getByRole("heading", { name: "Como embalar" })).toBeVisible();
  await expect(lista.getByText("2 volumes · uma etiqueta por volume")).toBeVisible();
  await expect(lista.getByText(/Volume 1 de 2 — Caixa P/)).toBeVisible();
  await expect(lista.getByText(/Volume 2 de 2 — Caixa P/)).toBeVisible();
  await expect(lista.getByText(/^6× /)).toBeVisible();
  await expect(lista.getByText(/^2× /)).toBeVisible();
  // O 2º volume dependeu de capacidade declarada: o selo e a dica avisam para conferir.
  await expect(lista.getByText("capacidade declarada pela loja")).toBeVisible();
  await expect(lista.getByText(/Confira ao fechar/)).toBeVisible();
  await expect(lista.getByRole("button", { name: "Imprimir lista" })).toBeVisible();
  await expect(page.getByRole("button", { name: "Despachar agora" })).toBeVisible();

  // Na impressão, só a lista: o resto da página (andamento, pagamento) some.
  await page.emulateMedia({ media: "print" });
  await expect(lista.getByText(/lista de embalagem/)).toBeVisible();
  await expect(lista.getByText(/Volume 1 de 2/)).toBeVisible();
  await expect(page.getByRole("heading", { name: "Andamento" })).toBeHidden();
  await expect(lista.getByRole("button", { name: "Imprimir lista" })).toBeHidden();
  await page.emulateMedia({ media: "screen" });
});

test("pedido despachado por volume: uma etiqueta e um rastreio para cada volume", async ({ page }) => {
  const ids = await samples(page);
  const tenantId = await openStore(page);
  await page.goto(`${PANEL}/t/${tenantId}/pedidos/${ids.shipped}`);

  await expect(page.getByText("Um código por volume (2), abaixo")).toBeVisible();
  const etiquetas = page.getByRole("list", { name: "Etiquetas por volume" });
  await expect(etiquetas.getByRole("listitem")).toHaveCount(2);
  await expect(etiquetas.getByText(/FKE2E1BR/)).toBeVisible();
  await expect(etiquetas.getByText(/FKE2E2BR/)).toBeVisible();
  await expect(etiquetas.getByRole("link", { name: "Imprimir etiqueta 1" })).toHaveAttribute(
    "href",
    /e2e-1\.pdf$/,
  );
  await expect(etiquetas.getByRole("link", { name: "Imprimir etiqueta 2" })).toBeVisible();
  // Já despachado: sem botão de compra.
  await expect(page.getByRole("button", { name: "Despachar agora" })).toHaveCount(0);
});
