import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";
import { requireCatalog } from "@/lib/store-access";
import { storefrontApi } from "@/lib/storefront-api";
import { type CategoryRef, isIndexable, type ProductPage, storeOrigin, type TagRef } from "@/lib/storefront";

import { ProductCard } from "../_store/product-card";
import { StoreShell } from "../_store/store-shell";
import styles from "../_store/store.module.css";

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
  const [products, categories, tags] = await Promise.all([
    storefrontApi<ProductPage>(context, "/catalog/products", {
      q: query,
      tag: selectedTag,
      cursor: cursor?.slice(0, 256),
      limit: "24",
    }),
    storefrontApi<CategoryRef[]>(context, "/catalog/categories"),
    storefrontApi<TagRef[]>(context, "/catalog/tags"),
  ]);
  const page = requireCatalog(products, "/loja");
  const roots = categories.kind === "ok" ? categories.data.filter((c) => !c.parent_id) : [];
  const tagList = tags.kind === "ok" ? tags.data : [];
  const next = new URLSearchParams();
  if (query) next.set("q", query);
  if (selectedTag) next.set("tag", selectedTag);
  if (page.next_cursor) next.set("cursor", page.next_cursor);

  return (
    <StoreShell context={context}>
      <h1>Produtos</h1>
      <form method="get" action="/loja">
        <input name="q" defaultValue={query ?? ""} placeholder="Buscar produtos" aria-label="Buscar produtos" minLength={2} />{" "}
        <button type="submit" className="button">
          Buscar
        </button>
      </form>
      {roots.length ? (
        <nav className={styles.nav} aria-label="Categorias">
          {roots.map((category) => (
            <Link key={category.id} href={`/loja/categoria/${category.slug}`}>
              {category.name}
            </Link>
          ))}
        </nav>
      ) : null}
      {tagList.length ? (
        <nav className={styles.nav} aria-label="Filtrar por tag">
          {tagList.map((item) =>
            item.slug === selectedTag ? (
              <Link key={item.slug} href="/loja" className={styles.tag} aria-current="true">
                {item.name} ×
              </Link>
            ) : (
              <Link key={item.slug} href={`/loja?tag=${encodeURIComponent(item.slug)}`} className={styles.tag}>
                {item.name}
              </Link>
            ),
          )}
        </nav>
      ) : null}
      {page.items.length === 0 ? <p>Nenhum produto encontrado.</p> : null}
      <div className={styles.grid}>
        {page.items.map((product) => (
          <ProductCard key={product.id} product={product} />
        ))}
      </div>
      {page.next_cursor ? (
        <p className={styles.section}>
          <Link href={`/loja?${next.toString()}`}>Mais produtos →</Link>
        </p>
      ) : null}
    </StoreShell>
  );
}
