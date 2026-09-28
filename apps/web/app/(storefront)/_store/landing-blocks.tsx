/**
 * Desenha os blocos da página inicial.
 *
 * Vive fora da página porque a prévia do painel usa este mesmo componente. Um renderizador só:
 * a prévia não diverge do que o cliente vê por construção, não por disciplina — se divergisse,
 * o lojista publicaria confiando numa tela que mente.
 *
 * Todo texto aqui vem do lojista e é escapado pelo React. Não existe `dangerouslySetInnerHTML`
 * perto de bloco nenhum; o único uso na vitrine é o JSON-LD, que escapa `<` na mão.
 *
 * O `variant` escolhe entre desenhos que este arquivo já tem. O bloco nunca traz CSS.
 */
import Link from "next/link";

import type { CategoryRef, LandingBlock, ProductCard as Card, StoreImage as Image } from "@/lib/storefront";

import { BlockIcon } from "./landing-icons";
import { ProductCard } from "./product-card";
import { StoreImage } from "./store-image";
import styles from "./store.module.css";
import { EmptyState, Grid, Section } from "./ui";

export interface BlockContext {
  catalogOn: boolean;
  chatUrl: string | null;
}

const DIAS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"];

function whatsappLink(e164: string): string {
  return `https://wa.me/${e164.replace(/\D/g, "")}`;
}

/** Para onde o botão de um bloco leva. Sem atendimento configurado, o chat não é oferecido. */
function ctaHref(target: unknown, context: BlockContext): string | null {
  if (target === "chat") return context.chatUrl;
  return context.catalogOn ? "/loja" : null;
}

function str(value: unknown): string | null {
  return typeof value === "string" && value ? value : null;
}

export function Block({
  block,
  first,
  context,
}: {
  block: LandingBlock;
  first: boolean;
  context: BlockContext;
}) {
  const title = str(block.title);
  const image = (block.image as Image | null | undefined) ?? null;
  const tone = str(block.tone) ?? "plain";
  const variant = str(block.variant);

  switch (block.type) {
    case "hero": {
      const href = ctaHref(block.cta_target, context);
      const label = str(block.cta_label);
      // O primeiro bloco da página é o `h1` do documento; os seguintes são seções.
      const Heading = first ? "h1" : "h2";
      const fundo = variant === "image_background" && image;
      return (
        <section
          className={fundo ? styles.heroFull : styles.hero}
          data-tone={tone}
          data-variant={variant ?? "image_right"}
        >
          {fundo ? (
            <StoreImage
              image={image}
              alt=""
              sizes="100vw"
              priority={first}
              className={styles.heroBackdrop}
            />
          ) : null}
          <div className={styles.heroText}>
            <Heading>{title}</Heading>
            {str(block.subtitle) ? <p className={styles.lead}>{String(block.subtitle)}</p> : null}
            {label && href ? (
              <a className="button" href={href}>
                {label}
              </a>
            ) : null}
          </div>
          {image && !fundo ? (
            <StoreImage
              image={image}
              alt={title ?? ""}
              sizes="(min-width: 760px) 520px, 100vw"
              priority={first}
              className={styles.heroPhoto}
            />
          ) : null}
        </section>
      );
    }

    case "announcement": {
      const href = ctaHref(block.link_target, context);
      const texto = str(block.text);
      if (!texto) return null;
      // Uma faixa, uma frase. Com destino, a faixa inteira é clicável.
      return (
        <aside className={styles.announcement} data-tone={tone}>
          {block.link_target !== "none" && href ? <a href={href}>{texto}</a> : <span>{texto}</span>}
        </aside>
      );
    }

    case "featured_products": {
      const products = (block.products as Card[] | undefined) ?? [];
      if (block.locked === true) return <LockedSection title={title} label="Entrar para ver os produtos" />;
      if (products.length === 0) return null;
      return (
        <Section title={title ?? undefined} actions={<Link href="/loja">Ver todos</Link>}>
          {variant === "carousel_scroll" ? (
            // Rolagem horizontal com encaixe, em CSS puro: nenhuma linha de JavaScript.
            <div className={styles.carousel}>
              {products.map((product) => (
                <ProductCard key={product.id} product={product} />
              ))}
            </div>
          ) : variant === "list" ? (
            <div className={styles.productList}>
              {products.map((product) => (
                <ProductCard key={product.id} product={product} />
              ))}
            </div>
          ) : (
            <Grid>
              {products.map((product) => (
                <ProductCard key={product.id} product={product} />
              ))}
            </Grid>
          )}
        </Section>
      );
    }

    case "categories": {
      const categories = (block.categories as CategoryRef[] | undefined) ?? [];
      if (block.locked === true) return <LockedSection title={title} label="Entrar para ver as categorias" />;
      if (categories.length === 0) return null;
      return (
        <Section title={title ?? undefined}>
          <nav
            className={variant === "pills" || !variant ? styles.chips : styles.catGrid}
            data-variant={variant ?? "pills"}
            aria-label={title ?? "Categorias"}
          >
            {categories.map((category) => (
              <Link
                key={category.id}
                href={`/loja/categoria/${category.slug}`}
                className={variant === "pills" || !variant ? styles.chip : styles.catCard}
              >
                {category.name}
              </Link>
            ))}
          </nav>
        </Section>
      );
    }

    case "benefits": {
      const items = (block.items as { icon: string; title: string; text?: string | null }[] | undefined) ?? [];
      if (items.length === 0) return null;
      return (
        <Section title={title ?? undefined} tone={tone}>
          <ul className={styles.benefits} data-variant={variant ?? "icons_row"}>
            {items.map((item, i) => (
              <li key={i}>
                <BlockIcon name={item.icon} className={styles.benefitIcon} />
                <span className={styles.benefitTitle}>{item.title}</span>
                {item.text ? <span className={styles.benefitText}>{item.text}</span> : null}
              </li>
            ))}
          </ul>
        </Section>
      );
    }

    case "text":
      return (
        <Section title={title ?? undefined} tone={tone}>
          <div className={styles.textBlock} data-variant={variant ?? "single"}>
            {image ? (
              <StoreImage
                image={image}
                alt={title ?? ""}
                sizes="(min-width: 760px) 720px, 100vw"
                className={variant === "with_image_side" ? styles.heroPhoto : styles.widePhoto}
              />
            ) : null}
            <p className={styles.description}>{String(block.body ?? "")}</p>
          </div>
        </Section>
      );

    case "gallery": {
      const images = (block.images as Image[] | undefined) ?? [];
      if (images.length === 0) return null;
      return (
        <Section title={title ?? undefined}>
          <div className={styles.gallery} data-variant={variant ?? "grid"}>
            {images.map((item, i) => (
              <StoreImage
                key={i}
                image={item}
                alt=""
                sizes="(min-width: 760px) 300px, 50vw"
                className={styles.photo}
              />
            ))}
          </div>
        </Section>
      );
    }

    case "testimonials": {
      const items = (block.items as { text: string; author?: string | null; source?: string | null }[] | undefined) ?? [];
      if (items.length === 0) return null;
      // Sem nota e sem marcação de avaliação: estrela que a própria loja publica sobre si é
      // penalizada em busca e convida a inventar número.
      return (
        <Section title={title ?? undefined}>
          <div className={styles.testimonials} data-variant={variant ?? "cards"}>
            {items.map((item, i) => (
              <figure key={i}>
                <blockquote>{item.text}</blockquote>
                {item.author ? (
                  <figcaption>
                    {item.author}
                    {item.source ? <span className="muted"> · {SOURCE_LABEL[item.source] ?? item.source}</span> : null}
                  </figcaption>
                ) : null}
              </figure>
            ))}
          </div>
        </Section>
      );
    }

    case "faq": {
      const items = (block.items as { question: string; answer: string }[] | undefined) ?? [];
      if (items.length === 0) return null;
      return (
        <Section title={title ?? undefined}>
          <div className={styles.faq} data-variant={variant ?? "accordion"}>
            {items.map((item, i) =>
              variant === "list" ? (
                <div key={i} className={styles.faqItem}>
                  <h3>{item.question}</h3>
                  <p>{item.answer}</p>
                </div>
              ) : (
                // `<details>` abre e fecha sem JavaScript, e o navegador cuida do teclado.
                <details key={i} className={styles.faqItem}>
                  <summary>{item.question}</summary>
                  <p>{item.answer}</p>
                </details>
              ),
            )}
          </div>
        </Section>
      );
    }

    case "hours": {
      const days = (block.days as { weekday: number; opens: string; closes: string }[] | undefined) ?? [];
      const address = str(block.address);
      const cidade = [str(block.city), str(block.state)].filter(Boolean).join(" - ");
      const busca = [address, cidade].filter(Boolean).join(", ");
      return (
        <Section title={title ?? "Onde e quando"} variant="card">
          {address ? (
            <p className={styles.where}>
              {/* Link de busca em vez de mapa embutido: `frame-src` do CSP não permite iframe,
                  e um mapa de terceiro entrega a visita de todo mundo a quem hospeda o mapa. */}
              <a href={`https://www.google.com/maps/search/?api=1&query=${encodeURIComponent(busca)}`} rel="noreferrer">
                {busca}
              </a>
            </p>
          ) : null}
          {days.length ? (
            <dl className={styles.hours} data-variant={variant ?? "table"}>
              {days.map((day, i) => (
                <div key={i}>
                  <dt>{DIAS[day.weekday] ?? ""}</dt>
                  <dd>
                    {day.opens} às {day.closes}
                  </dd>
                </div>
              ))}
            </dl>
          ) : null}
          {str(block.note) ? <p className="muted">{String(block.note)}</p> : null}
        </Section>
      );
    }

    case "contact":
      return (
        <Section title={title ?? "Contato"} variant="card">
          <ul className={styles.contactList} data-variant={variant ?? "list"}>
            {str(block.whatsapp_e164) ? (
              <li>
                WhatsApp: <a href={whatsappLink(String(block.whatsapp_e164))}>{String(block.whatsapp_e164)}</a>
              </li>
            ) : null}
            {str(block.instagram) ? (
              <li>
                Instagram:{" "}
                <a href={`https://instagram.com/${block.instagram}`} rel="noreferrer">
                  @{String(block.instagram)}
                </a>
              </li>
            ) : null}
            {str(block.email) ? (
              <li>
                E-mail: <a href={`mailto:${block.email}`}>{String(block.email)}</a>
              </li>
            ) : null}
            {str(block.address) ? <li>Endereço: {String(block.address)}</li> : null}
            {str(block.hours) ? <li>Horário: {String(block.hours)}</li> : null}
          </ul>
        </Section>
      );

    case "cta": {
      const href = ctaHref(block.cta_target, context);
      const label = str(block.cta_label);
      if (!href || !label) return null;
      return (
        <section className={styles.ctaBlock} data-tone={tone}>
          <div>
            <h2>{title}</h2>
            {str(block.subtitle) ? <p>{String(block.subtitle)}</p> : null}
          </div>
          <a className="button" href={href}>
            {label}
          </a>
        </section>
      );
    }

    default:
      // Bloco de um tipo que esta versão do site ainda não conhece (API mais nova, meio de um
      // deploy). Some da página em vez de derrubá-la.
      return null;
  }
}

const SOURCE_LABEL: Record<string, string> = {
  instagram: "Instagram",
  google: "Google",
  whatsapp: "WhatsApp",
  site: "site",
};

/** Bloco de catálogo numa loja que pede cadastro: fica a seção, sem um produto sequer. */
export function LockedSection({ title, label }: { title: string | null; label: string }) {
  return (
    <Section title={title ?? undefined}>
      <EmptyState
        title="Esta loja atende clientes cadastrados."
        action={
          <Link className="button" href="/entrar">
            {label}
          </Link>
        }
      >
        Entre com a sua conta para ver o que está à venda.
      </EmptyState>
    </Section>
  );
}

/** O JSON-LD de perguntas frequentes, quando a página tem esse bloco. */
export function faqJsonLd(blocks: LandingBlock[]): Record<string, unknown> | null {
  const items = blocks
    .filter((b) => b.type === "faq")
    .flatMap((b) => (b.items as { question: string; answer: string }[] | undefined) ?? []);
  if (items.length === 0) return null;
  return {
    "@context": "https://schema.org",
    "@type": "FAQPage",
    mainEntity: items.map((item) => ({
      "@type": "Question",
      name: item.question,
      acceptedAnswer: { "@type": "Answer", text: item.answer },
    })),
  };
}
