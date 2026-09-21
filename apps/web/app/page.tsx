import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";
import {
  type CategoryRef,
  isIndexable,
  jsonLd,
  type LandingBlock,
  type ProductCard as Card,
  type StoreImage as Image,
  storeOrigin,
} from "@/lib/storefront";

import { ProductCard } from "./_store/product-card";
import { StoreImage } from "./_store/store-image";
import { StoreShell } from "./_store/store-shell";
import styles from "./_store/store.module.css";

export async function generateMetadata(): Promise<Metadata> {
  const context = await getStorefrontContext();
  if (!context) return {};
  const seo = context.seo as { title?: string | null; description?: string | null; og_image_url?: string | null };
  const title = seo.title || context.tenant.name;
  const description = seo.description || `Loja online ${context.tenant.name}.`;
  const url = `${storeOrigin(context)}/`;
  return {
    title,
    description,
    alternates: { canonical: url },
    openGraph: {
      title,
      description,
      url,
      siteName: context.tenant.name,
      type: "website",
      locale: "pt_BR",
      images: seo.og_image_url ? [seo.og_image_url] : undefined,
    },
    robots: isIndexable(context) ? { index: true, follow: true } : { index: false, follow: false },
  };
}

function whatsappLink(e164: string): string {
  return `https://wa.me/${e164.replace(/\D/g, "")}`;
}

export default async function Home() {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const landing = await storefrontApi<LandingBlock[]>(context, "/landing");
  const blocks = landing.kind === "ok" ? landing.data : [];
  const catalogOn = Boolean(context.features.catalog);
  const branding = context.branding as { logo?: { url: string } | null };
  const organization = {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: context.tenant.name,
    url: `${storeOrigin(context)}/`,
    ...(branding.logo?.url ? { logo: branding.logo.url } : {}),
  };

  return (
    <StoreShell context={context}>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLd(organization) }} />
      {blocks.length === 0 ? (
        <section className={styles.hero}>
          <div>
            <h1>{context.tenant.name}</h1>
            {catalogOn ? (
              <Link className="button" href="/loja">
                Ver produtos
              </Link>
            ) : null}
          </div>
        </section>
      ) : null}
      {blocks.map((block, index) => (
        <Block key={index} block={block} first={index === 0} context={{ catalogOn, chatUrl: context.chatwoot_url ?? null }} />
      ))}
    </StoreShell>
  );
}

function Block({
  block,
  first,
  context,
}: {
  block: LandingBlock;
  first: boolean;
  context: { catalogOn: boolean; chatUrl: string | null };
}) {
  const title = typeof block.title === "string" ? block.title : null;
  const image = (block.image as Image | null | undefined) ?? null;
  switch (block.type) {
    case "hero": {
      const href = block.cta_target === "chat" && context.chatUrl ? context.chatUrl : "/loja";
      const showCta = typeof block.cta_label === "string" && (block.cta_target === "chat" ? Boolean(context.chatUrl) : context.catalogOn);
      const Heading = first ? "h1" : "h2";
      return (
        <section className={styles.hero}>
          <div>
            <Heading>{title}</Heading>
            {typeof block.subtitle === "string" ? <p className="muted">{block.subtitle}</p> : null}
            {showCta ? (
              <a className="button" href={href}>
                {String(block.cta_label)}
              </a>
            ) : null}
          </div>
          {image ? <StoreImage image={image} alt={title ?? ""} sizes="(min-width: 760px) 460px, 100vw" priority={first} className={styles.photo} /> : null}
        </section>
      );
    }
    case "featured_products": {
      const products = (block.products as Card[] | undefined) ?? [];
      if (products.length === 0) return null;
      return (
        <section className={styles.section}>
          <h2>{title}</h2>
          <div className={styles.grid}>
            {products.map((product) => (
              <ProductCard key={product.id} product={product} />
            ))}
          </div>
        </section>
      );
    }
    case "categories": {
      const categories = (block.categories as CategoryRef[] | undefined) ?? [];
      if (categories.length === 0) return null;
      return (
        <section className={styles.section}>
          <h2>{title}</h2>
          <nav className={styles.nav}>
            {categories.map((category) => (
              <Link key={category.id} href={`/loja/categoria/${category.slug}`}>
                {category.name}
              </Link>
            ))}
          </nav>
        </section>
      );
    }
    case "text":
      return (
        <section className={styles.section}>
          {title ? <h2>{title}</h2> : null}
          {image ? <StoreImage image={image} alt={title ?? ""} sizes="(min-width: 760px) 720px, 100vw" className={styles.photo} /> : null}
          <p className={styles.description}>{String(block.body ?? "")}</p>
        </section>
      );
    case "gallery": {
      const images = (block.images as Image[] | undefined) ?? [];
      if (images.length === 0) return null;
      return (
        <section className={styles.section}>
          {title ? <h2>{title}</h2> : null}
          <div className={styles.grid}>
            {images.map((item, i) => (
              <StoreImage key={i} image={item} alt="" sizes="(min-width: 760px) 240px, 50vw" className={styles.photo} />
            ))}
          </div>
        </section>
      );
    }
    case "contact":
      return (
        <section className={styles.section}>
          <h2>{title ?? "Contato"}</h2>
          <ul>
            {typeof block.whatsapp_e164 === "string" ? (
              <li>
                WhatsApp: <a href={whatsappLink(block.whatsapp_e164)}>{block.whatsapp_e164}</a>
              </li>
            ) : null}
            {typeof block.instagram === "string" ? (
              <li>
                Instagram:{" "}
                <a href={`https://instagram.com/${block.instagram}`} rel="noreferrer">
                  @{block.instagram}
                </a>
              </li>
            ) : null}
            {typeof block.email === "string" ? (
              <li>
                E-mail: <a href={`mailto:${block.email}`}>{block.email}</a>
              </li>
            ) : null}
            {typeof block.address === "string" ? <li>Endereço: {block.address}</li> : null}
            {typeof block.hours === "string" ? <li>Horário: {block.hours}</li> : null}
          </ul>
        </section>
      );
    default:
      return null;
  }
}
