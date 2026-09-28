"use client";

import { useEffect, useId, useRef, useState } from "react";

import { formatPrice, type StorePrice } from "@/lib/storefront";

import styles from "./store.module.css";

interface Suggestion {
  slug: string;
  name: string;
  price: StorePrice;
  image: string | null;
}

/** Tempo entre a última tecla e a busca: curto o bastante para parecer instantâneo. */
const DEBOUNCE_MS = 200;

/**
 * Busca do cabeçalho, com sugestões.
 *
 * O formulário por baixo é o de sempre: `GET /loja?q=`. Com o JavaScript desligado, ou antes de
 * esta ilha hidratar, buscar continua funcionando — o que se perde é a lista enquanto digita.
 *
 * O padrão de teclado é o de caixa com lista (`combobox`): setas andam, Enter abre o que está
 * marcado, Escape fecha, e Enter sem nada marcado envia o formulário. Sem isso seria uma caixa
 * bonita que ninguém usa sem mouse.
 */
export function SearchBox({ placeholder = "O que você procura?" }: { placeholder?: string }) {
  const [term, setTerm] = useState("");
  const [items, setItems] = useState<Suggestion[]>([]);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(-1);
  const listId = useId();
  const box = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const q = term.trim();
    if (q.length < 2) {
      setItems([]);
      setOpen(false);
      return;
    }
    // Uma busca por vez: quem digita rápido cancela a anterior em vez de empilhar respostas
    // que chegam fora de ordem.
    const controller = new AbortController();
    const timer = setTimeout(async () => {
      try {
        const response = await fetch(`/api/loja/busca?q=${encodeURIComponent(q)}`, {
          signal: controller.signal,
        });
        if (!response.ok) {
          setItems([]);
          return;
        }
        const data = (await response.json()) as { items?: Suggestion[] };
        setItems(data.items ?? []);
        setActive(-1);
        setOpen(true);
      } catch {
        // Cancelada ou rede fora: a caixa continua sendo um formulário que funciona.
        setItems([]);
      }
    }, DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
      controller.abort();
    };
  }, [term]);

  // Clicar fora fecha. Sem isso a lista acompanha a pessoa pela página inteira.
  useEffect(() => {
    if (!open) return;
    const onClick = (event: MouseEvent) => {
      if (!box.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onClick);
    return () => document.removeEventListener("mousedown", onClick);
  }, [open]);

  const showing = open && items.length > 0;

  return (
    <div className={styles.searchWrap} ref={box}>
      <form role="search" method="get" action="/loja" className={styles.search}>
        <input
          type="search"
          name="q"
          value={term}
          onChange={(event) => setTerm(event.target.value)}
          onFocus={() => items.length && setOpen(true)}
          onKeyDown={(event) => {
            if (!showing) return;
            if (event.key === "ArrowDown") setActive((i) => Math.min(i + 1, items.length - 1));
            else if (event.key === "ArrowUp") setActive((i) => Math.max(i - 1, -1));
            else if (event.key === "Escape") setOpen(false);
            else if (event.key === "Enter" && active >= 0) {
              // Só intercepta o Enter quando há sugestão marcada; caso contrário o formulário
              // envia e cai na página de resultados, como sempre.
              event.preventDefault();
              window.location.assign(`/loja/produto/${items[active]!.slug}`);
            } else return;
            if (event.key !== "Enter") event.preventDefault();
          }}
          aria-label="Buscar produtos"
          placeholder={placeholder}
          minLength={2}
          enterKeyHint="search"
          autoComplete="off"
          role="combobox"
          aria-expanded={showing}
          aria-controls={listId}
          aria-activedescendant={active >= 0 ? `${listId}-${active}` : undefined}
        />
        <button type="submit" className={styles.searchGo}>
          Buscar
        </button>
      </form>

      {showing ? (
        <ul className={styles.suggestions} id={listId} role="listbox" aria-label="Sugestões">
          {items.map((item, i) => (
            <li
              key={item.slug}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              className={styles.suggestion}
              data-active={i === active ? "true" : undefined}
            >
              <a href={`/loja/produto/${item.slug}`} onMouseEnter={() => setActive(i)}>
                {item.image ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={item.image} alt="" width={40} height={40} loading="lazy" decoding="async" />
                ) : (
                  <span className={styles.suggestionThumb} aria-hidden="true" />
                )}
                <span className={styles.suggestionName}>{item.name}</span>
                <span className={styles.suggestionPrice}>{formatPrice(item.price)}</span>
              </a>
            </li>
          ))}
        </ul>
      ) : null}
    </div>
  );
}
