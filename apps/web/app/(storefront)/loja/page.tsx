import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";
import { requireCatalog } from "@/lib/store-access";
import { storefrontApi } from "@/lib/storefront-api";
import { isIndexable, type ProductPage, storeOrigin, type TagRef } from "@/lib/storefront";

import { ProductCard } from "../_store/product-card";
import { StoreShell } from "../_store/store-shell";
import styles from "../_store/store.module.css";
import { Chip, EmptyState, FoldedChips, Grid, PageHead } from "../_store/ui";

export async function generateMetadata({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; cursor?: string; tag?: string }>;
}): Promise<Metadata> {
  const context = await getStorefrontContext();
  if (!context) return {};
  const { q, cursor, tag } = await searchParams;
  return {
    title: `Produtos · ${context.tenant.name}`,
    description: `Todos os produtos de ${context.tenant.name}.`,
    alternates: { canonical: `${storeOrigin(context)}/loja` },
    // Search results, tag filters and deeper pages are not separate documents for search engines.
    robots:
      isIndexable(context) && !q && !cursor && !tag ? { index: true, follow: true } : { index: false, follow: true },
  };
}

export default async function Store({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; cursor?: string; tag?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { q, cursor, tag } = await searchParams;
  const query = q && q.trim().length >= 2 ? q.trim().slice(0, 100) : undefined;
  const selectedTag = tag ? tag.slice(0, 80) : undefined;
  // Categorias não entram aqui: quem as desenha é a faixa do StoreShell, que já as busca.
  const [products, tags] = await Promise.all([
    storefrontApi<ProductPage>(context, "/catalog/products", {
      q: query,
      tag: selectedTag,
      cursor: cursor?.slice(0, 256),
      limit: "24",
    }),
    storefrontApi<TagRef[]>(context, "/catalog/tags"),
  ]);
  const page = requireCatalog(products, "/loja");
  const tagList = tags.kind === "ok" ? tags.data : [];
  const next = new URLSearchParams();
  if (query) next.set("q", query);
  if (selectedTag) next.set("tag", selectedTag);
  if (page.next_cursor) next.set("cursor", page.next_cursor);

  return (
    <StoreShell context={context}>
      <PageHead
        title={query ? `Resultados para "${query}"` : "Produtos"}
        lead={query || selectedTag ? `${page.items.length} ${page.items.length === 1 ? "item" : "itens"}` : undefined}
      />
      {/* Busca e categorias moram no cabeçalho, em toda página: repetir aqui era uma caixa
          igual e uma fileira igual à faixa logo acima. O filtro por tag fica, mas dobrado —
          numa loja com 19 etiquetas ele empurrava o primeiro produto para fora da tela do
          celular. */}
      {tagList.length ? (
        <FoldedChips label="Filtrar por tag" count={tagList.length} open={Boolean(selectedTag)}>
          {tagList.map((item) => (
            <Chip
              key={item.slug}
              href={item.slug === selectedTag ? "/loja" : `/loja?tag=${encodeURIComponent(item.slug)}`}
              active={item.slug === selectedTag}
            >
              {item.name}
            </Chip>
          ))}
        </FoldedChips>
      ) : null}
      {page.items.length === 0 ? (
        <EmptyState
          title="Nenhum produto encontrado."
          action={
            query || selectedTag ? (
              <Link className="button" href="/loja">
                Ver todos os produtos
              </Link>
            ) : null
          }
        >
          {query
            ? `Nada por aqui com "${query}". Tente outra palavra, ou veja tudo o que a loja tem.`
            : "A loja ainda não publicou produtos nesta seção."}
        </EmptyState>
      ) : (
        <Grid>
          {page.items.map((product) => (
            <ProductCard key={product.id} product={product} />
          ))}
        </Grid>
      )}
      {page.next_cursor ? (
        <p className={styles.section}>
          <Link className="button" href={`/loja?${next.toString()}`}>
            Ver mais produtos
          </Link>
        </p>
      ) : null}
    </StoreShell>
  );
}
