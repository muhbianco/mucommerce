import { cookies } from "next/headers";
import Link from "next/link";
import type { ReactNode } from "react";

import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import type { StorefrontContext } from "@/lib/tenant";

import styles from "./store.module.css";

interface Logo {
  url: string;
  width: number | null;
  height: number | null;
}

/**
 * Cabeçalho e rodapé de toda página da loja.
 *
 * Não abre uma `<div>` própria de propósito: o pai direto do `<header>` tem de ser o elemento do
 * layout que carrega as variáveis de marca. Um `<header>` por documento, e cada rótulo do
 * cabeçalho aparecendo uma vez só — é o que mantém os seletores por papel (`getByRole`) sem
 * ambiguidade.
 *
 * Aqui se lê cookie, mas nunca se chama a API: ler cookie não custa cacheabilidade nenhuma (a
 * página já é dinâmica), enquanto uma chamada autenticada por página custaria uma ida ao
 * servidor em toda visita.
 */
export async function StoreShell({ context, children }: { context: StorefrontContext; children: ReactNode }) {
  const signedIn = Boolean((await cookies()).get(CUSTOMER_SESSION_COOKIE));
  const branding = context.branding as { logo?: Logo | null };
  const logo = branding.logo;
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
        <span className={styles.spacer} />
        {context.features.catalog ? <Link href="/loja">Produtos</Link> : null}
        {context.features.catalog && context.features.events ? <Link href="/eventos">Eventos</Link> : null}
        {context.features.checkout ? <Link href="/carrinho">Carrinho</Link> : null}
        {signedIn ? <Link href="/conta">Minha conta</Link> : null}
        {signedIn ? (
          <form action="/auth/sair" method="post">
            <button type="submit" className={styles.linkButton}>
              Sair
            </button>
          </form>
        ) : null}
      </header>
      <main id="conteudo" className={styles.page}>
        {children}
      </main>
      <footer className={styles.footer}>{context.tenant.name} · loja online por MuhBianco</footer>
    </>
  );
}
