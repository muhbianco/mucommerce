import type { Metadata } from "next";
import Link from "next/link";
import { notFound, redirect } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";
import { type CategoryRef, isIndexable, jsonLd, type ProductPage, storeOrigin } from "@/lib/storefront";
import type { StorefrontContext } from "@/lib/tenant";

import { ProductCard } from "../../../_store/product-card";
import { StoreShell } from "../../../_store/store-shell";
import styles from "../../../_store/store.module.css";

async function load(context: StorefrontContext, slug: string) {
  const categories = await storefrontApi<CategoryRef[]>(context, "/catalog/categories");
  if (categories.kind !== "ok") return categories;
  const category = categories.data.find((c) => c.slug === slug);
  if (!category) return { kind: "not_found" as const };
  const parent = category.parent_id ? categories.data.find((c) => c.id === category.parent_id) : undefined;
  const children = categories.data.filter((c) => c.parent_id === category.id);
  return { kind: "ok" as const, category, parent, children };
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const context = await getStorefrontContext();
  if (!context) return {};
  const { slug } = await params;
  const found = await load(context, slug);
  if (found.kind !== "ok") return { robots: { index: false } };
  return {
    title: `${found.category.name} · ${context.tenant.name}`,
    description: found.category.description ?? `${found.category.name} em ${context.tenant.name}.`,
    alternates: { canonical: `${storeOrigin(context)}/loja/categoria/${found.category.slug}` },
    robots: isIndexable(context) ? { index: true, follow: true } : { index: false, follow: false },
  };
}

export default async function CategoryPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ cursor?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { slug } = await params;
  const { cursor } = await searchParams;
  const found = await load(context, slug);
  if (found.kind === "login_required") redirect("/loja");
  if (found.kind !== "ok") notFound();
  const products = await storefrontApi<ProductPage>(context, "/catalog/products", {
    category: found.category.slug,
    cursor: cursor?.slice(0, 256),
    limit: "24",
  });
  if (products.kind !== "ok") notFound();
  const origin = storeOrigin(context);
  const trail = [
    { name: "Produtos", url: `${origin}/loja` },
    ...(found.parent ? [{ name: found.parent.name, url: `${origin}/loja/categoria/${found.parent.slug}` }] : []),
    { name: found.category.name, url: `${origin}/loja/categoria/${found.category.slug}` },
  ];
  const breadcrumbs = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: trail.map((item, i) => ({ "@type": "ListItem", position: i + 1, name: item.name, item: item.url })),
  };

  return (
    <StoreShell context={context}>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLd(breadcrumbs) }} />
      <p className={styles.breadcrumb}>
        <Link href="/loja">Produtos</Link>
        {found.parent ? (
          <>
            {" › "}
            <Link href={`/loja/categoria/${found.parent.slug}`}>{found.parent.name}</Link>
          </>
        ) : null}
      </p>
      <h1>{found.category.name}</h1>
      {found.category.description ? <p className="muted">{found.category.description}</p> : null}
      {found.children.length ? (
        <nav className={styles.nav} aria-label="Subcategorias">
          {found.children.map((child) => (
            <Link key={child.id} href={`/loja/categoria/${child.slug}`}>
              {child.name}
            </Link>
          ))}
        </nav>
      ) : null}
      {products.data.items.length === 0 ? <p>Nenhum produto nesta categoria.</p> : null}
      <div className={styles.grid}>
        {products.data.items.map((product) => (
          <ProductCard key={product.id} product={product} />
        ))}
      </div>
      {products.data.next_cursor ? (
        <p className={styles.section}>
          <Link href={`/loja/categoria/${found.category.slug}?cursor=${encodeURIComponent(products.data.next_cursor)}`}>
            Mais produtos →
          </Link>
        </p>
      ) : null}
    </StoreShell>
  );
}
