import { headers } from "next/headers";
import Link from "next/link";
import { redirect } from "next/navigation";
import type { ReactNode } from "react";

import { requireMe } from "@/lib/panel/api";

import { logout } from "../actions";
import { panelFonts } from "../fonts";
import styles from "../panel.module.css";

const PANEL_HOST = process.env.PANEL_HOST ?? "painel.muhbianco.com.br";

/** Painel da loja no endereço da plataforma (o domínio próprio, quando existir, vem depois). */
function storePanelUrl(slug: string): string {
  return `https://${slug}.${PANEL_HOST}`;
}

// Authenticated shell. Links are relative to the panel host root: the middleware maps
// <host>/x to /painel/x, so URLs stay clean (padaria.painel.muhbianco.com.br/t/<id>).
export default async function PanelShell({ children }: { children: ReactNode }) {
  const me = await requireMe();
  const kind = (await headers()).get("x-host-kind");
  const platformHost = kind === "panel";

  // painel.muhbianco.com.br é da equipe MuhBianco (ADR 0014): lojista trabalha no painel da loja.
  if (platformHost && !me.platform_role) {
    const [only] = me.memberships;
    if (me.memberships.length === 1 && only) redirect(storePanelUrl(only.tenant_slug));
    return (
      <div className={`${styles.shell} ${panelFonts}`}>
        <main className={`${styles.content} ${styles.login}`}>
          <span className={styles.eyebrow}>Painel MuhBianco</span>
          <h1>Painel da sua loja</h1>
          {me.memberships.length === 0 ? (
            <p className="muted">Nenhuma loja vinculada à sua conta ainda.</p>
          ) : (
            <ul>
              {me.memberships.map((m) => (
                <li key={m.tenant_id}>
                  <a href={storePanelUrl(m.tenant_slug)}>{storePanelUrl(m.tenant_slug).replace("https://", "")}</a>
                </li>
              ))}
            </ul>
          )}
        </main>
      </div>
    );
  }

  return (
    <div className={`${styles.shell} ${panelFonts}`}>
      <header className={styles.topbar}>
        <Link href="/" className={styles.brand}>
          MuhBianco <span className={styles.brandTag}>{platformHost ? "Equipe" : "Painel"}</span>
        </Link>
        {platformHost ? <Link href="/">Lojas</Link> : null}
        {me.platform_role && platformHost ? <a href="https://muhbianco.com.br/admin.html#lojas">Admin do site</a> : null}
        <span className={styles.spacer} />
        <span className={styles.who}>{me.email}</span>
        <form action={logout}>
          <button type="submit" className={styles.buttonSmall}>
            Sair
          </button>
        </form>
      </header>
      <main className={styles.content}>{children}</main>
    </div>
  );
}
