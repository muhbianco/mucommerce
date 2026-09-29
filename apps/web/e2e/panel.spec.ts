import { expect, type Page, test } from "@playwright/test";

import { API, PANEL, STORE } from "./playwright.config";

async function signIn(page: Page): Promise<void> {
  await page.goto(`${PANEL}/`);
  await expect(page).toHaveURL(/\/entrar/);
  await page.getByRole("link", { name: "Entrar com Google" }).click();
  // fake-accounts → /sso/callback → continue page → home
  await expect(page.getByRole("heading", { name: /Olá, Admin E2E/ })).toBeVisible();
}

test("login com a conta MuhBianco, lojas visíveis para admin da plataforma", async ({ page, context }) => {
  await signIn(page);
  await expect(page.getByRole("link", { name: "MuhBianco", exact: true })).toBeVisible();
  await expect(page.getByRole("link", { name: "Loja Fechada", exact: true })).toBeVisible();

  const cookies = await context.cookies(PANEL);
  const session = cookies.filter((cookie) => cookie.name.startsWith("__Host-"));
  expect(session.map((cookie) => cookie.name).sort()).toEqual(["__Host-mb_at", "__Host-mb_rt"]);
  for (const cookie of session) {
    expect(cookie.httpOnly).toBe(true);
    expect(cookie.sameSite).toBe("Strict");
  }
});

test("código de login falso volta para a tela de entrar, no mesmo endereço", async ({ page }) => {
  await page.goto(`${PANEL}/sso/callback?code=forjado&state=qualquer`);
  // O host importa: a middleware reescreve para /painel/..., e o redirecionamento absoluto
  // levava para um endereço sem o `painel.` — 404 em vez do aviso de login.
  await expect(page).toHaveURL(`${PANEL}/entrar?erro=sso`);
  await expect(page.getByRole("link", { name: "Entrar com Google" })).toBeVisible();
});

test("criar produto; publicar sem imagem é recusado; vitrine não mostra", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  await page.getByRole("link", { name: "Produtos" }).first().click();

  const form = page.locator("form").filter({ has: page.getByRole("button", { name: /Criar/ }) });
  await form.locator('input[name="name"]').fill("Torta E2E");
  await form.locator('input[name="price"]').fill("25,00");
  await form.getByRole("button", { name: /Criar/ }).click();

  await expect(page.getByText("Produto criado.")).toBeVisible();
  await expect(page.locator('input[name="name"]').first()).toHaveValue("Torta E2E");

  await page.getByRole("button", { name: "Publicar" }).click();
  await expect(page.getByText(/produto sem imagem/)).toBeVisible();
  await expect(page.getByRole("button", { name: "Publicar" })).toBeVisible();

  const shop = await page.goto(`${STORE}/loja/produto/torta-e2e`);
  expect(shop?.status()).toBe(404);
});

test("pausar a venda deixa o produto indisponível na vitrine; opções geram variantes", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  await page.getByRole("link", { name: "Produtos" }).first().click();
  await page.getByRole("link", { name: "Bolo de cenoura" }).click();
  const productUrl = page.url().split("?")[0] ?? "";

  await page.getByLabel("Motivo (só você vê)").fill("forno em manutenção");
  await page.getByRole("button", { name: "Pausar venda" }).click();
  await expect(page.getByText(/Venda pausada: o produto segue/)).toBeVisible();
  await expect(page.getByText(/forno em manutenção/)).toBeVisible();

  await page.goto(`${STORE}/loja/produto/bolo-de-cenoura`);
  await expect(page.getByText("Indisponível").first()).toBeVisible();

  await page.goto(productUrl);
  await page.getByRole("button", { name: "Retomar venda" }).click();
  await expect(page.getByText("Venda retomada.")).toBeVisible();

  await page.getByLabel("Opção 1").fill("Tamanho");
  await page.getByLabel("Valores (separados por vírgula)").first().fill("Pequeno, Grande");
  await page.getByRole("button", { name: "Salvar opções" }).click();
  await expect(page.getByText("Opções salvas; variantes atualizadas.")).toBeVisible();
  await expect(page.getByText(/^Pequeno · P\d+$/)).toBeVisible();
  await expect(page.getByText(/^Grande · P\d+-1$/)).toBeVisible();
});

test("evento: o painel cria um lote dentro da capacidade", async ({ page }) => {
  await signIn(page);
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  await page.getByRole("link", { name: "Produtos" }).first().click();
  await page.getByRole("link", { name: "Oficina de Brownie" }).click();
  await expect(page.getByRole("heading", { name: "Evento e lotes" })).toBeVisible();
  await expect(page.getByText("Ingressos nos lotes: 30 de 40")).toBeVisible();

  await page.getByLabel("Novo lote").fill("Lote extra");
  const form = page.locator("form").filter({ has: page.getByRole("button", { name: "Criar lote" }) });
  await form.getByLabel("Preço").fill("90,00");
  await form.getByLabel("Ingressos").fill("5");
  await form.getByRole("button", { name: "Criar lote" }).click();
  await expect(page.getByText("Lote criado; o estoque de ingressos foi contado.")).toBeVisible();
  await expect(page.getByText("Ingressos nos lotes: 35 de 40")).toBeVisible();
});

test("brief da vitrine: cada passo salva sozinho e o passo seguinte não apaga o anterior", async ({
  page,
}) => {
  await signIn(page);
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  await page.getByRole("link", { name: "Página inicial" }).first().click();

  // A tela da vitrine oferece o questionário antes de qualquer coisa: quem abre aqui sem saber o
  // que preencher precisa achar essa porta.
  await expect(page.getByRole("heading", { name: "Deixe a gente montar para você" })).toBeVisible();
  await page.getByRole("link", { name: "Começar" }).click();

  await expect(page).toHaveURL(/\/vitrine\/brief\/negocio/);
  await page.getByLabel("O que a sua loja é").selectOption("padaria_confeitaria");
  await page.getByLabel("O que você vende").fill("bolo de pote e brownie por encomenda");
  await page.getByRole("button", { name: "Salvar e continuar" }).click();

  // O passo 2 manda só os campos dele. Se mandasse o brief inteiro, o que está acima sumiria.
  await expect(page).toHaveURL(/\/vitrine\/brief\/publico/);
  await expect(page.getByText("Respostas salvas.")).toBeVisible();
  await page.getByLabel("Por que de você, e não de outro (opcional)").fill("feito no dia\nentrego de bicicleta");
  await page.getByRole("button", { name: "Salvar e continuar" }).click();

  await expect(page).toHaveURL(/\/vitrine\/brief\/onde/);
  // O WhatsApp é guardado em E.164, mas ninguém digita assim: o painel traduz.
  await page.getByLabel("WhatsApp (opcional)").fill("31 98888-7777");
  await page.getByLabel("Cidade (opcional)").fill("Contagem");
  await page.getByRole("button", { name: "Salvar e continuar" }).click();

  await expect(page).toHaveURL(/\/vitrine\/brief\/jeito/);
  await expect(page.getByText("3 de 4")).toBeVisible();

  // Voltar ao primeiro passo mostra o que foi gravado lá, não um formulário em branco.
  // O passo respondido diz isso em palavras, não só com o visto: é o que o leitor de tela lê.
  await page.getByRole("link", { name: "O seu negócio — respondido" }).click();
  await expect(page.getByLabel("O que você vende")).toHaveValue("bolo de pote e brownie por encomenda");

  await page.getByRole("link", { name: "Onde te achar — respondido" }).click();
  await expect(page.getByLabel("WhatsApp (opcional)")).toHaveValue("+5531988887777");
  await expect(page.getByLabel("Cidade (opcional)")).toHaveValue("Contagem");
});

test("brief: WhatsApp impossível nomeia o campo, em vez de recusar a página inteira", async ({
  page,
}) => {
  await signIn(page);
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  await page.goto(`${page.url().split("?")[0]}/vitrine/brief/onde`);

  await page.getByLabel("WhatsApp (opcional)").fill("123");
  await page.getByRole("button", { name: /Salvar e/ }).click();
  await expect(page.getByText(/WhatsApp inválido/)).toBeVisible();
});

test("proposta de vitrine: pedir, ver a prévia, publicar e conferir na loja", async ({
  page,
  request,
}) => {
  await signIn(page);
  await page.getByRole("link", { name: "MuhBianco", exact: true }).click();
  const tenant = page.url().split("?")[0] ?? "";

  // O questionário precisa dizer o que a loja vende: sem isso não há página a escrever.
  await page.goto(`${tenant}/vitrine/brief/negocio`);
  await page.getByLabel("O que você vende").fill("brownie, cookie e bolo de pote");
  await page.getByRole("button", { name: "Salvar e continuar" }).click();

  await page.goto(`${tenant}/vitrine/propostas`);
  const antes = await page.getByText(/Você já usou \d+ de \d+ neste mês/).textContent();
  await page.getByRole("button", { name: "Montar uma proposta" }).click();
  await expect(page.getByText("Pedido recebido")).toBeVisible();
  // A cota é cobrada no pedido, não na entrega.
  await expect(page.getByText(/Você já usou \d+ de \d+ neste mês/)).not.toHaveText(antes ?? "");

  // O worker, encenado: a suíte não fala com modelo nenhum (nem teria chave).
  const built = await request.post(`${API}/__e2e/landing/build`, { data: { tenant: "muhbianco" } });
  expect((await built.json()).status).toBe("ready");

  await page.reload();
  await expect(page.getByRole("heading", { name: "Prévia" })).toBeVisible();
  // A prévia renderiza o componente de produção, pelo mesmo resolver da vitrine.
  await expect(page.getByText("Feito na hora, do jeito que você gosta")).toBeVisible();

  // Daqui em diante a loja modelo fica com esta página inicial, e não com a semeada. A suíte é
  // serial sobre um banco só de propósito (pedidos.spec depende do pedido de checkout.spec), e
  // o que vem depois foi conferido com esta página no ar.
  await page.getByRole("button", { name: "Publicar esta página" }).click();
  await expect(page.getByText("Proposta publicada")).toBeVisible();

  // E o que a lojista aprovou é o que o cliente vê.
  await page.goto(`${STORE}/`);
  await expect(page.getByRole("heading", { name: "Feito na hora, do jeito que você gosta" })).toBeVisible();
  await expect(page.getByText("Aceita Pix")).toBeVisible();
});

test("proposta: sem dizer o que a loja vende, o pedido é recusado com o motivo", async ({
  page,
}) => {
  await signIn(page);
  await page.getByRole("link", { name: "Loja Fechada", exact: true }).click();
  const tenant = page.url().split("?")[0] ?? "";

  await page.goto(`${tenant}/vitrine/propostas`);
  await page.getByRole("button", { name: "Montar uma proposta" }).click();
  await expect(page.getByText(/Conte pelo menos o que a sua loja vende/)).toBeVisible();
});
