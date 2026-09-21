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

/** Brand header + footer around every storefront page. */
export async function StoreShell({ context, children }: { context: StorefrontContext; children: ReactNode }) {
  // Public stores stay cacheable: only closed stores read the session cookie.
  const signedIn =
    context.access_mode !== "public" && Boolean((await cookies()).get(CUSTOMER_SESSION_COOKIE));
  const branding = context.branding as { primary_color?: string; logo?: Logo | null };
  const logo = branding.logo;
  return (
    <div style={{ ["--brand-primary" as string]: branding.primary_color ?? "#111111" }}>
      <header className={styles.header}>
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
        {signedIn ? <Link href="/conta">Minha conta</Link> : null}
        {signedIn ? (
          <form action="/auth/sair" method="post">
            <button type="submit" className={styles.linkButton}>
              Sair
            </button>
          </form>
        ) : null}
      </header>
      <main>{children}</main>
      <footer className={styles.footer}>
        {context.tenant.name} · loja online por MuhBianco
      </footer>
    </div>
  );
}
