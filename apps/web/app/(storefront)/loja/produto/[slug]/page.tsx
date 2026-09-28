import type { Metadata } from "next";
import Link from "next/link";
import { notFound, permanentRedirect } from "next/navigation";
import { cache } from "react";

import { getStorefrontContext } from "@/lib/server-context";
import { requireCatalog } from "@/lib/store-access";
import { storefrontApi } from "@/lib/storefront-api";
import {
  AVAILABILITY_LABEL,
  formatPrice,
  isIndexable,
  jsonLd,
  offSale,
  type ProductDetail,
  productOffers,
  withDefaults,
  storeOrigin,
} from "@/lib/storefront";

import { AddToCart, CART_ERRORS } from "../../../_store/add-to-cart";
import { StoreImage } from "../../../_store/store-image";
import { StoreShell } from "../../../_store/store-shell";
import styles from "../../../_store/store.module.css";
import { VariantPicker } from "../../../_store/variant-picker";

// Metadata and page share one API call per request. `cache` compares arguments by identity,
// so the key is the slug (a string), not the per-call parsed context object.
const loadProduct = cache(async (slug: string) => {
  const context = await getStorefrontContext();
  if (!context) return { kind: "not_found" as const };
  const result = await storefrontApi<ProductDetail>(
    context,
    `/catalog/products/${encodeURIComponent(slug.slice(0, 160))}`,
  );
  return result.kind === "ok" ? { ...result, data: withDefaults(result.data) } : result;
});

function shareImage(product: ProductDetail): string | undefined {
  const renditions = product.images[0]?.renditions ?? [];
  const fitting = renditions.filter((r) => r.width <= 1200);
  return (fitting[fitting.length - 1] ?? renditions[renditions.length - 1])?.url;
}

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
  const context = await getStorefrontContext();
  if (!context) return {};
  const { slug } = await params;
  const result = await loadProduct(slug);
  if (result.kind !== "ok") return { robots: { index: false } };
  const product = result.data;
  const title = product.seo.title || `${product.name} · ${context.tenant.name}`;
  const description =
    product.seo.description || product.short_description || `${product.name} em ${context.tenant.name}.`;
  const url = `${storeOrigin(context)}/loja/produto/${product.slug}`;
  const image = shareImage(product);
  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: { title, description, url, siteName: context.tenant.name, type: "website", images: image ? [image] : undefined },
    robots: isIndexable(context) ? { index: true, follow: true } : { index: false, follow: false },
  };
}

export default async function ProductPage({
  params,
  searchParams,
}: {
  params: Promise<{ slug: string }>;
  searchParams: Promise<{ erro?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { slug } = await params;
  const { erro } = await searchParams;
  const result = await loadProduct(slug);
  const product = requireCatalog(result, `/loja/produto/${encodeURIComponent(slug)}`);
  // A ticket's page is its event page (when the store runs events).
  if (product.kind === "ticket" && context.features.events) permanentRedirect(`/eventos/${product.slug}`);
  const origin = storeOrigin(context);
  const url = `${origin}/loja/produto/${product.slug}`;
  const category = product.categories[0];
  const sells = Boolean(context.features.checkout);
  const back = `/loja/produto/${product.slug}`;

  const structured = [
    {
      "@context": "https://schema.org",
      "@type": "Product",
      name: product.name,
      sku: product.sku,
      description: product.seo.description || product.short_description || undefined,
      image: product.images.map((image) => image.renditions[image.renditions.length - 1]?.url).filter(Boolean),
      brand: { "@type": "Brand", name: context.tenant.name },
      offers: productOffers(product, url),
    },
    {
      "@context": "https://schema.org",
      "@type": "BreadcrumbList",
      itemListElement: [
        { "@type": "ListItem", position: 1, name: "Produtos", item: `${origin}/loja` },
        ...(category
          ? [{ "@type": "ListItem", position: 2, name: category.name, item: `${origin}/loja/categoria/${category.slug}` }]
          : []),
        { "@type": "ListItem", position: category ? 3 : 2, name: product.name, item: url },
      ],
    },
  ];

  const [cover, ...others] = product.images;
  return (
    <StoreShell context={context}>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLd(structured) }} />
      <p className={styles.breadcrumb}>
        <Link href="/loja">Produtos</Link>
        {category ? (
          <>
            {" › "}
            <Link href={`/loja/categoria/${category.slug}`}>{category.name}</Link>
          </>
        ) : null}
      </p>
      <div className={styles.product}>
        <div>
          {cover ? (
            <StoreImage image={cover} alt={product.name} sizes="(min-width: 760px) 460px, 100vw" priority className={styles.photo} />
          ) : (
            <div className={styles.photo} aria-hidden="true" />
          )}
          {others.length ? (
            <div className={styles.thumbs}>
              {others.map((image, i) => (
                <StoreImage key={i} image={image} alt={`${product.name} (${i + 2})`} sizes="120px" className={styles.photo} />
              ))}
            </div>
          ) : null}
        </div>
        <div>
          <h1>{product.name}</h1>
          <p className={styles.price} style={{ fontSize: "1.4rem" }}>
            {formatPrice(product.price)}
            {product.price.compare_at_cents ? (
              <span className={styles.compare}>
                {formatPrice({ ...product.price, amount_cents: product.price.compare_at_cents })}
              </span>
            ) : null}
          </p>
          <p>
            <span className={offSale(product.availability) ? styles.soldOut : styles.tag}>
              {AVAILABILITY_LABEL[product.availability]}
            </span>
          </p>
          {product.short_description ? <p>{product.short_description}</p> : null}
          {product.tags.length ? (
            <p className={styles.nav}>
              {product.tags.map((tag) => (
                <Link key={tag.slug} href={`/loja?tag=${encodeURIComponent(tag.slug)}`} className={styles.tag}>
                  {tag.name}
                </Link>
              ))}
            </p>
          ) : null}
          {erro ? <p role="alert">{CART_ERRORS[erro] ?? "Não foi possível adicionar. Tente de novo."}</p> : null}
          {product.options.length || product.modifier_groups.length ? (
            <VariantPicker
              options={product.options}
              variants={product.variants}
              modifierGroups={product.modifier_groups}
              buy={sells ? { back } : undefined}
            />
          ) : product.variants.length > 1 ? (
            <ul>
              {product.variants.map((variant) => (
                <li key={variant.id}>
                  {variant.name}: {formatPrice(variant.price)} · {AVAILABILITY_LABEL[variant.availability]}
                  {sells && !offSale(variant.availability) ? (
                    <AddToCart variantId={variant.id} back={back} byWeight={product.sold_by === "weight"} />
                  ) : null}
                </li>
              ))}
            </ul>
          ) : sells && product.variants[0] ? (
            <AddToCart
              variantId={product.variants[0].id}
              back={back}
              disabled={offSale(product.availability)}
              byWeight={product.sold_by === "weight"}
            />
          ) : null}
          {product.description_md ? <div className={styles.description}>{product.description_md}</div> : null}
          {sells ? null : <p className="muted">Pedidos online chegam em breve.</p>}
        </div>
      </div>
    </StoreShell>
  );
}
