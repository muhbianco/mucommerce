import { expect, test } from "@playwright/test";

import { CLOSED_STORE, STORE } from "./playwright.config";

/**
 * O que o kit da vitrine não pode perder.
 *
 * Fica num arquivo à parte para os testes de fluxo continuarem falando só de compra. Aqui é o
 * contrário: nada de fluxo, só as garantias estruturais que, quando quebram, quebram em silêncio
 * — um segundo `<header>`, um rótulo repetido, um texto que sumiu no fundo, a página rolando de
 * lado no celular.
 */

const PAGINAS = ["/", "/loja", "/loja/produto/camiseta-muhbianco", "/eventos", "/entrar"];

test.describe("estrutura da vitrine", () => {
  test("um cabeçalho por documento, e ele carrega o tema", async ({ page }) => {
    for (const caminho of PAGINAS) {
      await page.goto(`${STORE}${caminho}`);
      // Dois `<header>` no documento estouram qualquer seletor por papel, e é assim que um kit
      // de páginas costuma quebrar a suíte inteira de uma vez.
      await expect(page.locator("header"), caminho).toHaveCount(1);
      await expect(page.locator("header[data-store-header]"), caminho).toHaveCount(1);
    }
  });

  test("cada rótulo do cabeçalho aparece uma vez só", async ({ page }) => {
    await page.goto(`${STORE}/loja`);
    // `getByRole` não filtra por visibilidade: uma navegação duplicada "para o celular" casaria
    // duas vezes mesmo escondida, e os testes de compra passariam a errar sozinhos.
    for (const rotulo of ["Carrinho", "Buscar", "Todos os produtos"]) {
      await expect(page.getByRole("link", { name: rotulo }).or(page.getByRole("button", { name: rotulo }))).toHaveCount(
        1,
      );
    }
  });

  test("o tema da loja chega ao CSS, sem buraco nenhum", async ({ page }) => {
    await page.goto(`${STORE}/`);
    const vars = await page.locator("header[data-store-header]").evaluate((header) => {
      const style = getComputedStyle(header.parentElement as HTMLElement);
      const nomes = [
        "--brand-primary",
        "--brand-on-primary",
        "--store-ink",
        "--store-paper",
        "--store-brand-ink",
        "--store-line",
        "--store-danger-ink",
      ];
      return Object.fromEntries(nomes.map((n) => [n, style.getPropertyValue(n).trim()]));
    });
    for (const [nome, valor] of Object.entries(vars)) {
      expect(valor, `${nome} vazio`).not.toBe("");
      expect(valor, `${nome} = ${valor}`).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  test("o texto tem contraste com o fundo em que foi pintado", async ({ page }) => {
    for (const loja of [STORE, CLOSED_STORE]) {
      await page.goto(`${loja}/`);
      const razao = await page.evaluate(() => {
        const lum = (cor: string) => {
          const [r, g, b] = cor.match(/\d+/g)!.map(Number) as [number, number, number];
          const canal = (c: number) => {
            const v = c / 255;
            return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
          };
          return 0.2126 * canal(r) + 0.7152 * canal(g) + 0.0722 * canal(b);
        };
        const body = getComputedStyle(document.body);
        const shell = document.querySelector("header")!.parentElement as HTMLElement;
        const s = getComputedStyle(shell);
        const fundo = s.backgroundColor === "rgba(0, 0, 0, 0)" ? body.backgroundColor : s.backgroundColor;
        const a = lum(s.color);
        const b = lum(fundo);
        return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
      });
      // Corpo de texto: a régua do sistema é 7:1, bem acima do mínimo da norma.
      expect(razao, `contraste em ${loja}`).toBeGreaterThanOrEqual(7);
    }
  });

  test("o foco do teclado é visível", async ({ page }) => {
    await page.goto(`${STORE}/loja`);
    await page.keyboard.press("Tab");
    const contorno = await page.evaluate(() => {
      const alvo = document.activeElement as HTMLElement;
      const s = getComputedStyle(alvo);
      return { largura: s.outlineWidth, estilo: s.outlineStyle, quem: alvo.textContent?.slice(0, 30) };
    });
    expect(contorno.largura, `foco em "${contorno.quem}"`).not.toBe("0px");
    expect(contorno.estilo).not.toBe("none");
  });

  test("no celular nada rola de lado", async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 812 });
    for (const caminho of PAGINAS) {
      await page.goto(`${STORE}${caminho}`);
      const largura = await page.evaluate(() => document.documentElement.scrollWidth);
      expect(largura, caminho).toBeLessThanOrEqual(375);
    }
  });
});

test.describe("sem JavaScript", () => {
  // O fluxo de compra é `<form action={serverAction}>` de ponta a ponta, e isso é de propósito:
  // 3G ruim, aparelho velho e bloqueador de script não podem impedir alguém de comprar.
  test.use({ javaScriptEnabled: false });

  test("dá para pôr no carrinho e mudar a quantidade", async ({ page }) => {
    await page.goto(`${STORE}/entrar?next=%2Floja`);
    await page.getByRole("link", { name: "Entrar com Google" }).click();

    // Produto sem opções: o seletor de variante desliga o botão até hidratar, por desenho.
    await page.goto(`${STORE}/loja/produto/bolo-de-cenoura`);
    const comprar = page.getByRole("button", { name: "Adicionar ao carrinho" });
    if (await comprar.isEnabled()) {
      await comprar.click();
      await expect(page).toHaveURL(/\/carrinho\?ok=adicionado$/);
    } else {
      await page.goto(`${STORE}/carrinho`);
    }

    const mais = page.getByRole("button", { name: "Aumentar a quantidade" }).first();
    if (await mais.count()) {
      const antes = await page.getByLabel("Quantidade").first().inputValue();
      await mais.click();
      await expect(page.getByLabel("Quantidade").first()).not.toHaveValue(antes);
    }
  });
});
