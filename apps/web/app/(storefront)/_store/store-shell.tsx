import { cookies } from "next/headers";
import Link from "next/link";
import type { ReactNode } from "react";

import { CART_COUNT_COOKIE, cartCountLabel, parseCartCount } from "@/lib/cart-count";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { storefrontApi } from "@/lib/storefront-api";
import type { CategoryRef } from "@/lib/storefront";
import type { StorefrontContext } from "@/lib/tenant";

import styles from "./store.module.css";

interface Logo {
  url: string;
  width: number | null;
  height: number | null;
}

/** Quantas categorias cabem na faixa antes de virar uma lista que ninguém percorre. */
const STRIP_LIMIT = 10;

/**
 * Cabeçalho e rodapé de toda página da loja.
 *
 * Não abre `<div>` própria de propósito: o pai direto do `<header>` tem de ser o elemento do
 * layout que carrega as variáveis de marca. Um `<header>` por documento, e cada rótulo do
 * cabeçalho aparecendo uma vez só.
 *
 * Aqui se lê cookie, mas nunca se chama a API do cliente: ler cookie não custa cacheabilidade
 * (a página já é dinâmica), enquanto uma chamada autenticada por página custaria uma ida ao
 * servidor em toda visita — inclusive na página de produto, onde o tempo de pintura decide a
 * venda. As categorias vêm da rota pública, que é cacheada por loja.
 *
 * Sem gaveta no celular: a faixa de categorias rola com o polegar, e é mais rápida do que abrir
 * e fechar um menu. Gaveta exigiria duplicar a navegação, e navegação duplicada é rótulo
 * repetido no documento.
 */
export async function StoreShell({ context, children }: { context: StorefrontContext; children: ReactNode }) {
  const jar = await cookies();
  const signedIn = Boolean(jar.get(CUSTOMER_SESSION_COOKIE));
  const cartCount = parseCartCount(jar.get(CART_COUNT_COOKIE)?.value);
  const branding = context.branding as { logo?: Logo | null };
  const logo = branding.logo;
  const catalogOn = Boolean(context.features.catalog);

  const categories = catalogOn ? await storefrontApi<CategoryRef[]>(context, "/catalog/categories") : null;
  const roots =
    categories?.kind === "ok" ? categories.data.filter((c) => !c.parent_id).slice(0, STRIP_LIMIT) : [];

  return (
    <>
      <a className={styles.skip} href="#conteudo">
        Ir para o conteúdo
      </a>
      <header className={styles.header} data-store-header>
        <Link href="/" className={styles.brand}>
          {logo ? (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={logo.url}
              alt={context.tenant.name}
              height={40}
              width={logo.width && logo.height ? Math.round((40 * logo.width) / logo.height) : undefined}
            />
          ) : (
            context.tenant.name
          )}
        </Link>

        {catalogOn ? (
          <form role="search" method="get" action="/loja" className={styles.search}>
            <input
              type="search"
              name="q"
              aria-label="Buscar produtos"
              placeholder="O que você procura?"
              minLength={2}
              enterKeyHint="search"
            />
            <button type="submit" className={styles.searchGo}>
              Buscar
            </button>
          </form>
        ) : (
          <span className={styles.spacer} />
        )}

        <nav className={styles.headerNav} aria-label="Sua conta">
          {signedIn ? <Link href="/conta">Minha conta</Link> : null}
          {context.features.checkout ? (
            <Link href="/carrinho" className={styles.cartLink}>
              Carrinho
              {cartCount ? (
                <span className={styles.cartCount} aria-label={`${cartCount} no carrinho`}>
                  {cartCountLabel(cartCount)}
                </span>
              ) : null}
            </Link>
          ) : null}
          {signedIn ? (
            <form action="/auth/sair" method="post">
              <button type="submit" className={styles.linkButton}>
                Sair
              </button>
            </form>
          ) : null}
        </nav>
      </header>

      {/* A faixa é a navegação do catálogo inteiro, não só das categorias: sem ela, uma loja
          que ainda não organizou nada ficaria sem caminho para os produtos e os eventos. */}
      {catalogOn ? (
        <nav className={styles.catStrip} aria-label="Navegação da loja">
          <Link href="/loja">Todos os produtos</Link>
          {roots.map((category) => (
            <Link key={category.id} href={`/loja/categoria/${category.slug}`}>
              {category.name}
            </Link>
          ))}
          {context.features.events ? <Link href="/eventos">Eventos</Link> : null}
        </nav>
      ) : null}

      <main id="conteudo" className={styles.page}>
        {children}
      </main>

      <footer className={styles.footer}>
        <nav className={styles.footerNav} aria-label="Sobre a loja">
          <Link href="/politicas/termos">Termos de uso</Link>
          <Link href="/politicas/privacidade">Política de privacidade</Link>
        </nav>
        <p>{context.tenant.name} · loja online por MuhBianco</p>
      </footer>
    </>
  );
}
