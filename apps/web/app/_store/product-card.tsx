import Link from "next/link";

import { AVAILABILITY_LABEL, formatPrice, type ProductCard as Card } from "@/lib/storefront";

import { StoreImage } from "./store-image";
import styles from "./store.module.css";

export function ProductCard({ product }: { product: Card }) {
  return (
    <Link href={`/loja/produto/${product.slug}`} className={styles.card}>
      {product.image ? (
        <StoreImage
          image={product.image}
          alt={product.name}
          sizes="(min-width: 760px) 240px, 50vw"
        />
      ) : (
        <div className={styles.photo} aria-hidden="true" />
      )}
      <strong>{product.name}</strong>
      <span className={styles.price}>
        {formatPrice(product.price)}
        {product.price.compare_at_cents ? (
          <span className={styles.compare}>
            {formatPrice({ ...product.price, amount_cents: product.price.compare_at_cents })}
          </span>
        ) : null}
      </span>
      {product.availability !== "available" ? (
        <span className={product.availability === "sold_out" ? styles.soldOut : styles.tag}>
          {AVAILABILITY_LABEL[product.availability]}
        </span>
      ) : null}
    </Link>
  );
}
