"use client";

import { useEffect, useRef, useState } from "react";

import { srcSet, type StoreImage as Image } from "@/lib/storefront";

import styles from "./store.module.css";

/**
 * Galeria do produto.
 *
 * O caminho sem JavaScript já funciona: o servidor manda todas as fotos empilhadas com
 * âncoras, e as miniaturas são links para elas. Esta ilha melhora o que existe — troca a foto
 * grande sem recarregar, anda com as setas do teclado e amplia num `<dialog>`, que é o único
 * jeito de fazer modal com foco preso e Escape sem escrever nada disso à mão.
 *
 * `<dialog>` em vez de uma `div` com `position: fixed`: o navegador cuida da camada superior,
 * do fundo, do foco e do Escape. Menos código nosso é menos jeito de errar acessibilidade.
 */
export function Gallery({ images, alt }: { images: Image[]; alt: string }) {
  const [current, setCurrent] = useState(0);
  const dialog = useRef<HTMLDialogElement>(null);
  const [zoomed, setZoomed] = useState(false);

  // Produto trocado (navegação entre páginas reusa o componente): volta para a primeira foto.
  useEffect(() => setCurrent(0), [images]);

  if (images.length === 0) return <div className={styles.photo} aria-hidden="true" />;
  const image = images[Math.min(current, images.length - 1)]!;
  const largest = image.renditions[image.renditions.length - 1];

  const move = (delta: number) => setCurrent((i) => (i + delta + images.length) % images.length);

  const open = () => {
    setZoomed(true);
    dialog.current?.showModal();
  };

  return (
    <div className={styles.gallery3}>
      <button
        type="button"
        className={styles.galleryMain}
        onClick={open}
        aria-label={`Ampliar a foto ${current + 1} de ${images.length}`}
      >
        {/* eslint-disable-next-line @next/next/no-img-element */}
        <img
          src={largest?.url}
          srcSet={srcSet(image)}
          sizes="(min-width: 900px) 620px, 100vw"
          width={image.width ?? largest?.width}
          height={image.height ?? largest?.height}
          alt={image.alt ?? alt}
          // A primeira é o maior elemento da tela: carrega cedo, nunca preguiçosa.
          loading={current === 0 ? "eager" : "lazy"}
          decoding="async"
          fetchPriority={current === 0 ? "high" : undefined}
        />
      </button>

      {images.length > 1 ? (
        <div
          className={styles.thumbs}
          role="tablist"
          aria-label="Fotos do produto"
          onKeyDown={(event) => {
            if (event.key === "ArrowRight") move(1);
            else if (event.key === "ArrowLeft") move(-1);
            else return;
            event.preventDefault();
          }}
        >
          {images.map((thumb, i) => (
            <button
              key={i}
              type="button"
              role="tab"
              aria-selected={i === current}
              aria-label={`Foto ${i + 1}`}
              className={styles.thumbButton}
              onClick={() => setCurrent(i)}
            >
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                src={thumb.renditions[0]?.url}
                alt=""
                width={thumb.width ?? 160}
                height={thumb.height ?? 160}
                loading="lazy"
                decoding="async"
              />
            </button>
          ))}
        </div>
      ) : null}

      <dialog ref={dialog} className={styles.zoom} onClose={() => setZoomed(false)}>
        {zoomed ? (
          <>
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img src={largest?.url} srcSet={srcSet(image)} sizes="100vw" alt={image.alt ?? alt} />
            <form method="dialog">
              <button type="submit" aria-label="Fechar">
                Fechar
              </button>
            </form>
          </>
        ) : null}
      </dialog>
    </div>
  );
}
