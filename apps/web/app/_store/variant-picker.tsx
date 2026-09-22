"use client";

import { useState } from "react";

import {
  AVAILABILITY_LABEL,
  findVariant,
  formatPrice,
  modifierRule,
  modifiersTotal,
  offSale,
  type ProductOption,
  type StoreModifierGroup,
  type StoreVariant,
} from "@/lib/storefront";

import styles from "./store.module.css";

/**
 * One radio group per option and one group per set of modifiers; shows the chosen combination's
 * price (plus modifiers) and availability. Display only: orders are priced by the API.
 */
export function VariantPicker({
  options,
  variants,
  modifierGroups,
}: {
  options: ProductOption[];
  variants: StoreVariant[];
  modifierGroups: StoreModifierGroup[];
}) {
  const first = variants.find((v) => v.availability === "available") ?? variants[0];
  const [selection, setSelection] = useState<Record<string, string>>(first?.option_values ?? {});
  const [extras, setExtras] = useState<string[]>(() =>
    modifierGroups.flatMap((group) => (group.min_select > 0 ? group.modifiers.slice(0, group.min_select) : [])).map((m) => m.id),
  );
  const chosen = options.length ? findVariant(variants, selection) : variants[0];
  const extra = modifiersTotal(modifierGroups, extras);

  // Radios only for "choose exactly one"; an optional single choice is a checkbox that can be
  // cleared, and picking another one in a max-1 group replaces it.
  const isRadio = (group: StoreModifierGroup) => group.min_select === 1 && group.max_select === 1;

  function toggle(group: StoreModifierGroup, id: string): void {
    if (extras.includes(id)) {
      if (!isRadio(group)) setExtras(extras.filter((x) => x !== id));
      return;
    }
    const inGroup = new Set(group.modifiers.map((m) => m.id));
    setExtras(group.max_select === 1 ? [...extras.filter((x) => !inGroup.has(x)), id] : [...extras, id]);
  }

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
      {modifierGroups.map((group) => {
        const taken = group.modifiers.filter((m) => extras.includes(m.id)).length;
        const radio = isRadio(group);
        return (
          <fieldset key={group.id}>
            <legend>
              {group.name} <span className={styles.muted}>{modifierRule(group)}</span>
            </legend>
            {group.modifiers.map((modifier) => {
              const checked = extras.includes(modifier.id);
              return (
                <label key={modifier.id}>
                  <input
                    type={radio ? "radio" : "checkbox"}
                    name={`modificador-${group.id}`}
                    value={modifier.id}
                    checked={checked}
                    disabled={group.max_select > 1 && !checked && taken >= group.max_select}
                    onChange={() => toggle(group, modifier.id)}
                  />
                  {modifier.name}
                  {modifier.price_cents ? ` (+${formatPrice({ ...(chosen ?? first)!.price, amount_cents: modifier.price_cents })})` : ""}
                </label>
              );
            })}
          </fieldset>
        );
      })}
      <p role="status">
        {chosen ? (
          <>
            <strong>{formatPrice({ ...chosen.price, amount_cents: chosen.price.amount_cents + extra })}</strong>{" "}
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
