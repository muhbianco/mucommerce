import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import { cache } from "react";

import { getStorefrontContext } from "@/lib/server-context";
import { requireCatalog } from "@/lib/store-access";
import { storefrontApi } from "@/lib/storefront-api";
import {
  EVENT_AVAILABILITY_LABEL,
  type EventDetail,
  eventJsonLd,
  formatEventDate,
  formatPrice,
  isIndexable,
  jsonLd,
  LOT_STATE_LABEL,
  storeOrigin,
} from "@/lib/storefront";

import { AddToCart, CART_ERRORS } from "../../_store/add-to-cart";
import { StoreImage } from "../../_store/store-image";
import { StoreShell } from "../../_store/store-shell";
import styles from "../../_store/store.module.css";

// Metadata and page share one API call per request (cache keyed by the slug string).
const loadEvent = cache(async (slug: string) => {
  const context = await getStorefrontContext();
  if (!context || !context.features.events)
    return { kind: "not_found" as const };
  return storefrontApi<EventDetail>(
    context,
    `/catalog/events/${encodeURIComponent(slug.slice(0, 160))}`,
  );
});

export async function generateMetadata({
  params,
}: {
  params: Promise<{ slug: string }>;
}): Promise<Metadata> {
  const context = await getStorefrontContext();
  if (!context) return {};
  const { slug } = await params;
  const result = await loadEvent(slug);
  if (result.kind !== "ok") return { robots: { index: false } };
  const event = result.data;
  const title = event.seo.title || `${event.name} · ${context.tenant.name}`;
  const description =
    event.seo.description ||
    event.short_description ||
    `${event.name}: ${formatEventDate(event.starts_at, event.ends_at, context.tenant.timezone)}.`;
  const url = `${storeOrigin(context)}/eventos/${event.slug}`;
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
    },
    robots: isIndexable(context)
      ? { index: true, follow: true }
      : { index: false, follow: false },
  };
}

/**
 * A janela de vendas em uma linha que cabe na tela.
 *
 * Antes saía "vendas de 10/10/2026, 14:00 a 25/10/2026, 23:00": quatro linhas numa coluna
 * estreita, e o que importa — até quando dá para comprar — ficava no fim. O ano some quando é
 * o corrente (quem compra ingresso para daqui a duas semanas não precisa dele) e a abertura
 * some quando as vendas já começaram: "vendas até 25/10, 23:00" é a informação que decide.
 */
function salesWindow(
  start: string | null,
  end: string | null,
  timeZone: string,
): string {
  const agora = new Date();
  const curto = (iso: string) => {
    const data = new Date(iso);
    const mesmoAno = data.getFullYear() === agora.getFullYear();
    return new Intl.DateTimeFormat("pt-BR", {
      day: "2-digit",
      month: "2-digit",
      ...(mesmoAno ? {} : { year: "numeric" }),
      hour: "2-digit",
      minute: "2-digit",
      timeZone,
    }).format(data);
  };
  const abriu = start ? new Date(start) <= agora : true;
  if (start && end) {
    return abriu
      ? `vendas até ${curto(end)}`
      : `vendas de ${curto(start)} a ${curto(end)}`;
  }
  if (start) return abriu ? "" : `vendas a partir de ${curto(start)}`;
  if (end) return `vendas até ${curto(end)}`;
  return "";
}

export default async function EventPage({
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
  const event = requireCatalog(
    await loadEvent(slug),
    `/eventos/${encodeURIComponent(slug)}`,
  );
  const origin = storeOrigin(context);
  const url = `${origin}/eventos/${event.slug}`;
  const zone = context.tenant.timezone;
  const [cover] = event.images;
  const structured = eventJsonLd(event, url, {
    name: context.tenant.name,
    url: origin,
  });
  const sells = Boolean(context.features.checkout);

  return (
    <StoreShell context={context}>
      <script
        type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: jsonLd(structured) }}
      />
      <p className={styles.breadcrumb}>
        <Link href="/eventos">Eventos</Link>
      </p>
      <div className={styles.product}>
        <div>
          {cover ? (
            <StoreImage
              image={cover}
              alt={event.name}
              sizes="(min-width: 760px) 460px, 100vw"
              priority
              className={styles.photo}
            />
          ) : (
            <div className={styles.photo} aria-hidden="true" />
          )}
        </div>
        <div>
          <h1>{event.name}</h1>
          <p>
            <strong>
              {formatEventDate(event.starts_at, event.ends_at, zone)}
            </strong>
          </p>
          {event.venue_name ? (
            <p>
              {event.venue_name}
              {event.venue_address ? ` · ${event.venue_address}` : ""}
              {event.city ? ` · ${event.city}` : ""}
            </p>
          ) : null}
          {event.online ? (
            <p className="muted">
              Evento online: o link chega para quem comprar o ingresso.
            </p>
          ) : null}
          <p>
            <span
              className={
                event.availability === "on_sale" ? styles.tag : styles.soldOut
              }
            >
              {EVENT_AVAILABILITY_LABEL[event.availability]}
            </span>
            {event.status_note ? ` ${event.status_note}` : ""}
          </p>
          {event.short_description ? <p>{event.short_description}</p> : null}
          {/* Como o ingresso chega, antes de pagar: é a pergunta que decide a compra de quem
              nunca comprou nesta loja. */}
          {event.delivery_note ? (
            <p className={styles.deliveryNote}>{event.delivery_note}</p>
          ) : null}
          {erro ? (
            <p role="alert">
              {CART_ERRORS[erro] ??
                "Não foi possível adicionar. Tente de novo."}
            </p>
          ) : null}
          {event.lots.length ? (
            <section className={styles.lotsSection}>
              <h2 className={styles.lotsTitle}>Ingressos</h2>
              <ul className={styles.lotList}>
                {event.lots.map((lot) => {
                  const onSale = lot.state === "on_sale";
                  const janela = salesWindow(
                    lot.sales_starts_at,
                    lot.sales_ends_at,
                    zone,
                  );
                  return (
                    <li
                      key={lot.id}
                      className={onSale ? styles.lot : styles.lotOff}
                    >
                      <div className={styles.lotHead}>
                        <span className={styles.lotName}>{lot.name}</span>
                        <strong className={styles.lotPrice}>
                          {formatPrice(lot.price)}
                        </strong>
                      </div>
                      <p className={styles.lotMeta}>
                        <span className={onSale ? styles.tag : styles.soldOut}>
                          {LOT_STATE_LABEL[lot.state]}
                        </span>
                        {janela ? (
                          <span className={styles.lotWindow}>{janela}</span>
                        ) : null}
                      </p>
                      {sells && onSale ? (
                        <AddToCart
                          variantId={lot.variant_id}
                          back={`/eventos/${event.slug}`}
                        />
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            </section>
          ) : null}
          {event.description_md ? (
            <div className={styles.description}>{event.description_md}</div>
          ) : null}
          {sells ? null : (
            <p className="muted">Compra de ingressos online chega em breve.</p>
          )}
        </div>
      </div>
    </StoreShell>
  );
}
