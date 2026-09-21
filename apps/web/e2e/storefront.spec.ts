import { expect, test } from "@playwright/test";

import { CLOSED_STORE, STORE } from "./playwright.config";

test.describe("vitrine pública (loja modelo)", () => {
  test("landing, catálogo e produto com JSON-LD e canonical", async ({ page }) => {
    const landing = await page.goto(`${STORE}/`);
    expect(landing?.status()).toBe(200);

    await page.goto(`${STORE}/loja`);
    await expect(page.getByText("Brownie de chocolate")).toBeVisible();
    await expect(page.getByText("Cookie de aveia")).toBeVisible();

    const product = await page.goto(`${STORE}/loja/produto/brownie-de-chocolate`);
    expect(product?.status()).toBe(200);
    await expect(page.getByRole("heading", { name: "Brownie de chocolate" })).toBeVisible();
    await expect(page.getByText(/R\$\s*15,00/).first()).toBeVisible();

    const jsonLd = await page.locator('script[type="application/ld+json"]').allTextContents();
    const types = jsonLd.flatMap((text) => {
      const data = JSON.parse(text) as { "@type"?: string } | { "@type"?: string }[];
      return (Array.isArray(data) ? data : [data]).map((item) => item["@type"]);
    });
    expect(types).toContain("Product");
    await expect(page.locator('link[rel="canonical"]')).toHaveAttribute(
      "href",
      /\/loja\/produto\/brownie-de-chocolate$/,
    );
  });

  test("produto inexistente é 404", async ({ page }) => {
    const response = await page.goto(`${STORE}/loja/produto/nao-existe`);
    expect(response?.status()).toBe(404);
  });

  // Through the browser: only Chromium resolves `*.localhost`, Node's request context does not.
  test("robots e sitemap por host", async ({ page }) => {
    const robots = await page.goto(`${STORE}/robots.txt`);
    expect(robots?.status()).toBe(200);
    expect(await robots?.text()).toContain("User-Agent: *");
    const sitemap = await page.goto(`${STORE}/sitemap.xml`);
    expect(sitemap?.status()).toBe(200);
  });
});

test.describe("loja fechada (whitelist)", () => {
  test("landing abre, catálogo pede login", async ({ page }) => {
    const landing = await page.goto(`${CLOSED_STORE}/`);
    expect(landing?.status()).toBe(200);

    await page.goto(`${CLOSED_STORE}/loja`);
    await expect(page).toHaveURL(/\/entrar\?next=%2Floja$/);
  });

  test("a API do catálogo recusa sem sessão aprovada", async ({ request }) => {
    const response = await request.get("http://127.0.0.1:8791/api/v1/storefront/catalog/products", {
      headers: { "X-Tenant-Host": "fechada.loja.localhost", "X-Internal-Token": "e2e-web-token" },
    });
    expect(response.status()).toBe(401);
  });
});

test("host desconhecido é 404", async ({ page }) => {
  const response = await page.goto(`${STORE.replace("loja.localhost", "ninguem.localhost")}/`);
  expect(response?.status()).toBe(404);
});
