import { expect, test } from "@playwright/test";

import { FAKE_GOOGLE, STORE } from "./playwright.config";

// One shared seeded database: the purchase runs in order (cart → checkout → payment).
test.describe.configure({ mode: "serial" });

test("carrinho: entra para comprar, escolhe tamanho e adicional, retira na loja", async ({ page }) => {
  await page.request.post(`${FAKE_GOOGLE}/__e2e/identity`, {
    data: { sub: "e2e-bia", email: "bia.e2e@example.com", name: "Bia E2E" },
  });
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
