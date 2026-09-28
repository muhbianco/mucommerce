import { NextResponse } from "next/server";

import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";
import type { ProductPage } from "@/lib/storefront";

/**
 * Sugestões enquanto a pessoa digita no cabeçalho.
 *
 * Mora aqui, e não no api-commerce, porque o navegador não alcança a API interna (rede
 * privada, token interno) e o CSP da loja só permite buscar do próprio site. Nada de novo do
 * lado do servidor: é a mesma busca por `q` que a página `/loja` já usa.
 *
 * O ponto delicado é o acesso. Uma loja fechada que responde JSON de catálogo vira porta de
 * raspagem — e é justamente o que a suíte de vazamento protege na API. Então aqui vale a
 * mesma regra das páginas: sem sessão aprovada, 401 ou 403, e **nunca** um resultado parcial.
 */

export const dynamic = "force-dynamic";

const LIMIT = "6";

export async function GET(request: Request): Promise<NextResponse> {
  const context = await getStorefrontContext();
  if (!context || !context.features.catalog) {
    return NextResponse.json({ error: { code: "not_found" } }, { status: 404 });
  }

  const q = new URL(request.url).searchParams.get("q")?.trim() ?? "";
  // Menos de duas letras casa com a loja inteira: não é sugestão, é listagem.
  if (q.length < 2) return NextResponse.json({ items: [] }, { headers: noStore() });

  const result = await storefrontApi<ProductPage>(context, "/catalog/products", {
    q: q.slice(0, 100),
    limit: LIMIT,
  });

  if (result.kind === "login_required") {
    return NextResponse.json({ error: { code: "login_required" } }, { status: 401, headers: noStore() });
  }
  if (result.kind !== "ok") {
    return NextResponse.json({ error: { code: result.kind } }, { status: 403, headers: noStore() });
  }

  // Só o que a lista de sugestões mostra. O resto está na página do produto.
  const items = result.data.items.map((product) => ({
    slug: product.slug,
    name: product.name,
    price: product.price,
    image: product.image?.renditions[0]?.url ?? null,
  }));

  const headers = new Headers({ "X-Robots-Tag": "noindex" });
  // Loja pública: meio minuto de cache serve para quem digita a mesma palavra. As outras,
  // nunca — a resposta depende de quem está perguntando.
  headers.set("Cache-Control", context.access_mode === "public" ? "public, max-age=30" : "private, no-store");
  return NextResponse.json({ items }, { headers });
}

function noStore(): Headers {
  return new Headers({ "Cache-Control": "private, no-store", "X-Robots-Tag": "noindex" });
}
