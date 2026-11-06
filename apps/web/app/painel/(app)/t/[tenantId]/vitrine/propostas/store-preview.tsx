/**
 * A loja inteira como ficaria com uma proposta: cabeçalho, página inicial, vitrine, compra e
 * rodapé.
 *
 * Não é uma imitação: são os mesmos componentes e o mesmo `store.module.css` da vitrine, com o
 * tema (`storeStyle`) e os desenhos por área (`storeAttributes`) da proposta. A home vem do
 * mesmo resolvedor que a loja usa. O que não dá para mostrar sem um cliente de verdade — um
 * carrinho, um pedido — aparece como amostra com os mesmos estilos, e é dito como amostra.
 *
 * Por que não um iframe da loja: o CSP manda `frame-ancestors 'none'`, e a proposta ainda não
 * está publicada — não existe URL da loja que a mostre.
 */
import type { CSSProperties } from "react";

import type { ProductCard as Card } from "@/lib/storefront";
import { type Branding, type StoreDesign, storeAttributes, storeStyle } from "@/lib/theme";

import { storeFonts } from "../../../../../../(storefront)/_store/fonts";
import { Block as StoreBlock } from "../../../../../../(storefront)/_store/landing-blocks";
import { ProductCard } from "../../../../../../(storefront)/_store/product-card";
import storeStyles from "../../../../../../(storefront)/_store/store.module.css";
import vitrine from "../vitrine.module.css";
import local from "./propostas.module.css";

type Resolved = Record<string, unknown> & { type: string };

export function StorePreview({
  branding,
  design,
  blocks,
  storeName,
  logoUrl,
  catalogOn,
  phone,
}: {
  branding: Branding;
  design: StoreDesign;
  blocks: Resolved[];
  storeName: string;
  logoUrl: string | null;
  catalogOn: boolean;
  phone: boolean;
}) {
  const style = storeStyle(branding, design) as CSSProperties;
  const produtos = blocks
    .filter((b) => b.type === "featured_products")
    .flatMap((b) => (b.products as Card[] | undefined) ?? [])
    .slice(0, 4);
  const categorias = blocks
    .filter((b) => b.type === "categories")
    .flatMap((b) => (b.categories as { id: string; name: string }[] | undefined) ?? [])
    .slice(0, 5);
  const primeiro = produtos[0];

  return (
    <div className={phone ? `${vitrine.canvas} ${vitrine.canvasPhone} ${local.canvasTall}` : `${vitrine.canvas} ${local.canvasTall}`}>
      <div
        className={`${storeStyles.storefront} ${storeFonts} ${local.previewInner}`}
        style={style}
        {...storeAttributes(branding, design)}
      >
        <header className={storeStyles.header}>
          <span className={storeStyles.brand}>
            {logoUrl ? (
              // eslint-disable-next-line @next/next/no-img-element
              <img src={logoUrl} alt={storeName} height={branding.logo_height_px ?? 40} />
            ) : (
              storeName
            )}
          </span>
          <span className={storeStyles.spacer} />
          <nav className={storeStyles.headerNav} aria-label="Amostra do cabeçalho">
            <a href="#" tabIndex={-1}>
              Carrinho
            </a>
          </nav>
        </header>
        {catalogOn ? (
          <nav className={storeStyles.catStrip} aria-label="Amostra do menu">
            <a href="#" tabIndex={-1}>
              Todos os produtos
            </a>
            {categorias.map((c) => (
              <a key={c.id} href="#" tabIndex={-1}>
                {c.name}
              </a>
            ))}
          </nav>
        ) : null}

        <div className={storeStyles.page}>
          {blocks.map((block, i) => (
            <StoreBlock
              key={typeof block.id === "string" ? block.id : i}
              block={block}
              first={i === 0}
              context={{ catalogOn, chatUrl: null }}
            />
          ))}
        </div>

        {produtos.length ? (
          <div className={storeStyles.page}>
            <p className={local.sampleLabel}>Vitrine e categorias</p>
            <div className={storeStyles.grid}>
              {produtos.map((p) => (
                <ProductCard key={p.id} product={p} />
              ))}
            </div>
          </div>
        ) : null}

        {/* `data-flow="purchase"`: o mesmo gancho do carrinho e do checkout de verdade, para o
            estilo "compra neutra" aparecer aqui como vai aparecer lá. */}
        <div className={storeStyles.page} data-flow="purchase">
          <p className={local.sampleLabel}>Carrinho e checkout (amostra)</p>
          <h2>Seu carrinho</h2>
          <div className={storeStyles.card}>
            <p>
              <strong>{primeiro?.name ?? "Produto da loja"}</strong>
            </p>
            <p className="muted">1 unidade · frete calculado no checkout</p>
            <p className={`${storeStyles.price} ${storeStyles.priceLg}`}>
              {primeiro ? primeiroPreco(primeiro) : "R$ 0,00"}
            </p>
            <span className={`button ${storeStyles.checkoutCta}`}>Fechar pedido</span>
          </div>
        </div>

        <footer className={storeStyles.footer}>
          <p className={storeStyles.footerBrand} aria-hidden="true">
            {storeName}
          </p>
          <p>{storeName} · loja online por MuhBianco</p>
        </footer>
      </div>
    </div>
  );
}

function primeiroPreco(card: Card): string {
  const { amount_cents, currency } = card.price;
  return new Intl.NumberFormat("pt-BR", { style: "currency", currency }).format(amount_cents / 100);
}

/** As cores de uma proposta, como amostras com o código ao lado. */
export function Swatch({ color, label }: { color: string; label: string }) {
  return (
    <span className={local.swatch}>
      <span className={local.swatchDot} style={{ background: color }} aria-hidden="true" />
      <span>
        {label} <code>{color}</code>
      </span>
    </span>
  );
}

