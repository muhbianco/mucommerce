import type { Metadata } from "next";
import Link from "next/link";

import { api, requireMe } from "@/lib/panel/api";
import type { Page, TenantListItem } from "@/lib/panel/types";

import styles from "../panel.module.css";

export const metadata: Metadata = { title: "Painel MuhBianco" };

const SITE_ADMIN_STORES = "https://muhbianco.com.br/admin.html#lojas";

export default async function PanelHome() {
  const me = await requireMe();
  // Platform staff (MuhBianco admins) can open any store; stores are created in the site admin.
  const all = me.platform_role ? await api<Page<TenantListItem>>("/ops/tenants?limit=100") : null;
  const mine = new Map(me.memberships.map((m) => [m.tenant_id, m.role]));
  const rows = all
    ? all.items.map((t) => ({ id: t.id, name: t.name, slug: t.slug, status: t.status, role: mine.get(t.id) ?? "plataforma" }))
    : me.memberships.map((m) => ({ id: m.tenant_id, name: m.tenant_slug, slug: m.tenant_slug, status: "", role: m.role }));

  return (
    <>
      <h1>Olá, {me.full_name || me.email}</h1>
      <section className={styles.card}>
        <h2>Suas lojas</h2>
        {rows.length === 0 ? (
          <p className="muted">Nenhuma loja vinculada à sua conta ainda.</p>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Loja</th>
                <th>Papel</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr key={row.id}>
                  <td>
                    <Link href={`/t/${row.id}`}>{row.name}</Link> <small className="muted">{row.slug}</small>
                  </td>
                  <td>
                    <span className={styles.badge}>{row.role}</span>
                  </td>
                  <td>{row.status}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {me.platform_role ? (
          <p className="muted">
            Criar lojas, definir o dono, módulos e acesso: <a href={SITE_ADMIN_STORES}>admin MuhBianco → Lojas</a>.
          </p>
        ) : null}
      </section>
    </>
  );
}
