import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";
import { type CategoryRef, isIndexable, type ProductPage, storeOrigin } from "@/lib/storefront";

import { ProductCard } from "../_store/product-card";
import { StoreShell } from "../_store/store-shell";
import styles from "../_store/store.module.css";

export async function generateMetadata({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; cursor?: string }>;
}): Promise<Metadata> {
  const context = await getStorefrontContext();
  if (!context) return {};
  const { q, cursor } = await searchParams;
  return {
    title: `Produtos · ${context.tenant.name}`,
    description: `Todos os produtos de ${context.tenant.name}.`,
    alternates: { canonical: `${storeOrigin(context)}/loja` },
    // Search results and deeper pages are not separate documents for search engines.
    robots: isIndexable(context) && !q && !cursor ? { index: true, follow: true } : { index: false, follow: true },
  };
}

export default async function Store({
  searchParams,
}: {
  searchParams: Promise<{ q?: string; cursor?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { q, cursor } = await searchParams;
  const query = q && q.trim().length >= 2 ? q.trim().slice(0, 100) : undefined;
  const [products, categories] = await Promise.all([
    storefrontApi<ProductPage>(context, "/catalog/products", { q: query, cursor: cursor?.slice(0, 256), limit: "24" }),
    storefrontApi<CategoryRef[]>(context, "/catalog/categories"),
  ]);
  if (products.kind === "not_found") notFound();
  if (products.kind === "login_required") {
    return (
      <StoreShell context={context}>
        <h1>{context.tenant.name}</h1>
        <p>Esta loja é exclusiva para clientes cadastrados.</p>
        <p>
          <Link className="button" href="/entrar">
            Entrar
          </Link>
        </p>
      </StoreShell>
    );
  }
  const roots = categories.kind === "ok" ? categories.data.filter((c) => !c.parent_id) : [];
  const next = new URLSearchParams();
  if (query) next.set("q", query);
  if (products.data.next_cursor) next.set("cursor", products.data.next_cursor);

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
      {products.data.items.length === 0 ? <p>Nenhum produto encontrado.</p> : null}
      <div className={styles.grid}>
        {products.data.items.map((product) => (
          <ProductCard key={product.id} product={product} />
        ))}
      </div>
      {products.data.next_cursor ? (
        <p className={styles.section}>
          <Link href={`/loja?${next.toString()}`}>Mais produtos →</Link>
        </p>
      ) : null}
    </StoreShell>
  );
}
