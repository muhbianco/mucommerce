import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";
import { requireCatalog } from "@/lib/store-access";
import { storefrontApi } from "@/lib/storefront-api";
import { type EventPage, isIndexable, storeOrigin } from "@/lib/storefront";

import { EventCard } from "../_store/event-card";
import { StoreShell } from "../_store/store-shell";
import styles from "../_store/store.module.css";
import { EmptyState, Grid, PageHead } from "../_store/ui";

export async function generateMetadata({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string }>;
}): Promise<Metadata> {
  const context = await getStorefrontContext();
  if (!context) return {};
  const { cursor } = await searchParams;
  return {
    title: `Eventos · ${context.tenant.name}`,
    description: `Próximos eventos de ${context.tenant.name}.`,
    alternates: { canonical: `${storeOrigin(context)}/eventos` },
    robots: isIndexable(context) && !cursor ? { index: true, follow: true } : { index: false, follow: true },
  };
}

export default async function Events({ searchParams }: { searchParams: Promise<{ cursor?: string }> }) {
  const context = await getStorefrontContext();
  if (!context || !context.features.events) notFound();
  const { cursor } = await searchParams;
  const result = await storefrontApi<EventPage>(context, "/catalog/events", {
    cursor: cursor?.slice(0, 256),
    limit: "24",
  });
  const page = requireCatalog(result, "/eventos");

  return (
    <StoreShell context={context}>
      <PageHead title="Eventos" />
      {page.items.length === 0 ? (
        <EmptyState title="Nenhum evento marcado por enquanto.">
          Quando a loja marcar o próximo, ele aparece aqui com data, local e os lotes à venda.
        </EmptyState>
      ) : (
        <Grid>
          {page.items.map((event) => (
            <EventCard key={event.slug} event={event} timeZone={context.tenant.timezone} />
          ))}
        </Grid>
      )}
      {page.next_cursor ? (
        <p className={styles.section}>
          <Link className="button" href={`/eventos?cursor=${encodeURIComponent(page.next_cursor)}`}>
            Ver mais eventos
          </Link>
        </p>
      ) : null}
    </StoreShell>
  );
}
