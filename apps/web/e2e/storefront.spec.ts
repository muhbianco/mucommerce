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

  test("variantes: o tamanho escolhido mostra preço e disponibilidade; a tag filtra", async ({ page }) => {
    await page.goto(`${STORE}/loja/produto/camiseta-muhbianco`);
    const status = page.getByRole("status");
    await expect(page.getByRole("group", { name: "Tamanho" })).toBeVisible();
    await expect(status).toContainText(/R\$\s*59,00/);
    await expect(status).toContainText("Disponível");

    await page.getByRole("radio", { name: "G" }).check();
    await expect(status).toContainText(/R\$\s*69,00/);
    await page.getByRole("radio", { name: "M" }).check();
    await expect(status).toContainText("Indisponível"); // paused in the seed

    const scripts = await page.locator('script[type="application/ld+json"]').allTextContents();
    const items = scripts.flatMap((text) => {
      const data = JSON.parse(text) as Record<string, unknown> | Record<string, unknown>[];
      return Array.isArray(data) ? data : [data];
    });
    const product = items.find((item) => item["@type"] === "Product") as { offers?: { "@type"?: string } };
    expect(product.offers?.["@type"]).toBe("AggregateOffer");

    await page.getByRole("link", { name: "algodão" }).first().click();
    await expect(page).toHaveURL(/\/loja\?tag=algodao$/);
    await expect(page.getByText("Camiseta MuhBianco")).toBeVisible();
    await expect(page.getByText("Brownie de chocolate")).toHaveCount(0);
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

test("loja suspensa responde 503 com página neutra", async ({ page }) => {
  const response = await page.goto(STORE.replace("loja.localhost", "suspensa.loja.localhost") + "/");
  expect(response?.status()).toBe(503);
  await expect(page.getByRole("heading", { name: "Loja temporariamente indisponível" })).toBeVisible();
});

test("cada loja usa a própria marca (cor e fonte)", async ({ page }) => {
  const theme = async (url: string) => {
    await page.goto(url);
    return page.locator("header").evaluate((header) => {
      const shell = getComputedStyle(header.parentElement as HTMLElement);
      return { primary: shell.getPropertyValue("--brand-primary").trim(), font: shell.fontFamily };
    });
  };
  const modelo = await theme(`${STORE}/`);
  const fechada = await theme(`${CLOSED_STORE}/`);
  expect(modelo.primary).toBe("#111111");
  expect(fechada.primary).toBe("#2e7d32");
  expect(fechada.font).toContain("Georgia");
  expect(modelo.font).not.toContain("Georgia");
});
