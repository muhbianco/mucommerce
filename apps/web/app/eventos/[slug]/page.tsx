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

import { StoreImage } from "../../_store/store-image";
import { StoreShell } from "../../_store/store-shell";
import styles from "../../_store/store.module.css";

// Metadata and page share one API call per request (cache keyed by the slug string).
const loadEvent = cache(async (slug: string) => {
  const context = await getStorefrontContext();
  if (!context || !context.features.events) return { kind: "not_found" as const };
  return storefrontApi<EventDetail>(context, `/catalog/events/${encodeURIComponent(slug.slice(0, 160))}`);
});

export async function generateMetadata({ params }: { params: Promise<{ slug: string }> }): Promise<Metadata> {
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
    openGraph: { title, description, url, siteName: context.tenant.name, type: "website" },
    robots: isIndexable(context) ? { index: true, follow: true } : { index: false, follow: false },
  };
}

function salesWindow(start: string | null, end: string | null, timeZone: string): string {
  const format = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone });
  if (start && end) return `vendas de ${format.format(new Date(start))} a ${format.format(new Date(end))}`;
  if (start) return `vendas a partir de ${format.format(new Date(start))}`;
  if (end) return `vendas até ${format.format(new Date(end))}`;
  return "";
}

export default async function EventPage({ params }: { params: Promise<{ slug: string }> }) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { slug } = await params;
  const event = requireCatalog(await loadEvent(slug), `/eventos/${encodeURIComponent(slug)}`);
  const origin = storeOrigin(context);
  const url = `${origin}/eventos/${event.slug}`;
  const zone = context.tenant.timezone;
  const [cover] = event.images;
  const structured = eventJsonLd(event, url, { name: context.tenant.name, url: origin });

  return (
    <StoreShell context={context}>
      <script type="application/ld+json" dangerouslySetInnerHTML={{ __html: jsonLd(structured) }} />
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
            <strong>{formatEventDate(event.starts_at, event.ends_at, zone)}</strong>
          </p>
          {event.venue_name ? (
            <p>
              {event.venue_name}
              {event.venue_address ? ` · ${event.venue_address}` : ""}
              {event.city ? ` · ${event.city}` : ""}
            </p>
          ) : null}
          {event.online ? <p className="muted">Evento online: o link chega para quem comprar o ingresso.</p> : null}
          <p>
            <span className={event.availability === "on_sale" ? styles.tag : styles.soldOut}>
              {EVENT_AVAILABILITY_LABEL[event.availability]}
            </span>
            {event.status_note ? ` ${event.status_note}` : ""}
          </p>
          {event.short_description ? <p>{event.short_description}</p> : null}
          {event.lots.length ? (
            <table className={styles.lots}>
              <caption>Ingressos</caption>
              <tbody>
                {event.lots.map((lot) => (
                  <tr key={lot.id}>
                    <th scope="row">{lot.name}</th>
                    <td>{formatPrice(lot.price)}</td>
                    <td>
                      <span className={lot.state === "on_sale" ? styles.tag : styles.soldOut}>
                        {LOT_STATE_LABEL[lot.state]}
                      </span>
                    </td>
                    <td className="muted">{salesWindow(lot.sales_starts_at, lot.sales_ends_at, zone)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : null}
          {event.description_md ? <div className={styles.description}>{event.description_md}</div> : null}
          <p className="muted">Compra de ingressos online chega em breve.</p>
        </div>
      </div>
    </StoreShell>
  );
}
