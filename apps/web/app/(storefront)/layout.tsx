import type { ReactNode } from "react";

import { getStorefrontContext } from "@/lib/server-context";
import { type Branding, themeVariables } from "@/lib/theme";

import { storeFonts } from "./_store/fonts";
import styles from "./_store/store.module.css";

/**
 * Casca de toda página da loja: é aqui que a marca do lojista vira CSS.
 *
 * Fica no layout, e não dentro de cada página, para que um `loading.tsx` troque só o miolo — com
 * a casca na página, o cabeçalho sumiria e voltaria a cada navegação.
 *
 * O contexto pode faltar: um `notFound()` lançado lá dentro renderiza o 404 por dentro deste
 * layout. Sem tenant não há marca, e a página sai com o tema neutro do `globals.css`.
 */
export default async function StorefrontLayout({ children }: { children: ReactNode }) {
  const context = await getStorefrontContext();
  if (!context) return <>{children}</>;
  const branding = context.branding as Branding;
  return (
    <div className={`${styles.storefront} ${storeFonts}`} style={themeVariables(branding)}>
      {children}
    </div>
  );
}
