import Link from "next/link";
import type { ReactNode } from "react";

import { requireMe } from "@/lib/panel/api";

import { logout } from "../actions";
import styles from "../panel.module.css";

// Authenticated shell. Links are relative to the panel host root: the middleware maps
// painel.<domain>/x to /painel/x, so URLs stay clean (painel.muhbianco.com.br/t/<id>).
export default async function PanelShell({ children }: { children: ReactNode }) {
  const me = await requireMe();
  return (
    <div className={styles.shell}>
      <header className={styles.topbar}>
        <Link href="/" className={styles.brand}>
          Painel MuhBianco
        </Link>
        <Link href="/">Lojas</Link>
        {me.platform_role ? <a href="https://muhbianco.com.br/admin.html#lojas">Lojas (admin)</a> : null}
        <span className={styles.spacer} />
        <span>{me.email}</span>
        <form action={logout}>
          <button type="submit" className={styles.buttonGhost}>
            Sair
          </button>
        </form>
      </header>
      <main className={styles.content}>{children}</main>
    </div>
  );
}
