"use client";

import { useState } from "react";

import {
  AVAILABILITY_LABEL,
  findVariant,
  formatPrice,
  offSale,
  type ProductOption,
  type StoreVariant,
} from "@/lib/storefront";

import styles from "./store.module.css";

/** One radio group per option; shows the price and availability of the chosen combination. */
export function VariantPicker({ options, variants }: { options: ProductOption[]; variants: StoreVariant[] }) {
  const first = variants.find((v) => v.availability === "available") ?? variants[0];
  const [selection, setSelection] = useState<Record<string, string>>(first?.option_values ?? {});
  const chosen = findVariant(variants, selection);

  return (
    <div className={styles.picker}>
      {options.map((option) => (
        <fieldset key={option.name}>
          <legend>{option.name}</legend>
          {option.values.map((value) => {
            const candidate = findVariant(variants, { ...selection, [option.name]: value });
            return (
              <label key={value} className={candidate && !offSale(candidate.availability) ? undefined : styles.muted}>
                <input
                  type="radio"
                  name={option.name}
                  value={value}
                  checked={selection[option.name] === value}
                  onChange={() => setSelection({ ...selection, [option.name]: value })}
                />
                {value}
              </label>
            );
          })}
        </fieldset>
      ))}
      <p role="status">
        {chosen ? (
          <>
            <strong>{formatPrice(chosen.price)}</strong>{" "}
            <span className={offSale(chosen.availability) ? styles.soldOut : styles.tag}>
              {AVAILABILITY_LABEL[chosen.availability]}
            </span>
          </>
        ) : (
          <span className={styles.soldOut}>{AVAILABILITY_LABEL.unavailable}</span>
        )}
      </p>
    </div>
  );
}
