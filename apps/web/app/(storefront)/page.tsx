import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";
import { isIndexable, jsonLd, type LandingBlock, storeOrigin } from "@/lib/storefront";

import { Block, faqJsonLd } from "./_store/landing-blocks";
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

export default async function Home() {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const landing = await storefrontApi<LandingBlock[]>(context, "/landing");
  const blocks = landing.kind === "ok" ? landing.data : [];
  const catalogOn = Boolean(context.features.catalog);
  const branding = context.branding as { logo?: { url: string } | null };
  // Perguntas frequentes rendem em busca: viram `FAQPage` quando a página tem o bloco.
  const perguntas = faqJsonLd(blocks);
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
            <p className={styles.lead}>
              {catalogOn
                ? "Veja o que a loja tem disponível agora."
                : "Esta loja está montando a vitrine. Volte em breve."}
            </p>
            {catalogOn ? (
              <Link className="button" href="/loja">
                Ver produtos
              </Link>
            ) : null}
          </div>
        </section>
      ) : null}
      {perguntas ? (
        <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLd(perguntas) }} />
      ) : null}
      {blocks.map((block, index) => (
        <Block
          // O id do bloco é estável entre edições; a posição na lista não é. Com `index` como
          // chave, reordenar no painel faria o React reaproveitar o estado do bloco errado.
          key={typeof block.id === "string" ? block.id : index}
          block={block}
          first={index === 0}
          context={{ catalogOn, chatUrl: context.chatwoot_url ?? null }}
        />
      ))}
    </StoreShell>
  );
}
