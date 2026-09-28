import Link from "next/link";

import { EVENT_AVAILABILITY_LABEL, type EventCard as Card, formatEventDate, formatPrice } from "@/lib/storefront";

import { StoreImage } from "./store-image";
import styles from "./store.module.css";

export function EventCard({ event, timeZone }: { event: Card; timeZone: string }) {
  const open = event.availability === "on_sale";
  return (
    <Link href={`/eventos/${event.slug}`} className={styles.card}>
      {event.image ? (
        <StoreImage image={event.image} alt={event.name} sizes="(min-width: 760px) 240px, 50vw" />
      ) : (
        <div className={styles.photo} aria-hidden="true" />
      )}
      <strong>{event.name}</strong>
      <span>{formatEventDate(event.starts_at, null, timeZone)}</span>
      <span className="muted">
        {[event.venue_name, event.city].filter(Boolean).join(" · ") || (event.online ? "Online" : "")}
      </span>
      {event.price_from ? <span className={styles.price}>a partir de {formatPrice(event.price_from)}</span> : null}
      <span className={open ? styles.tag : styles.soldOut}>{EVENT_AVAILABILITY_LABEL[event.availability]}</span>
    </Link>
  );
}
