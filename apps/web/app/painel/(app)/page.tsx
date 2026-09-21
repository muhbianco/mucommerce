import type { Metadata } from "next";
import Link from "next/link";

import { requireMe } from "@/lib/panel/api";

import styles from "../panel.module.css";

export const metadata: Metadata = { title: "Painel MuhBianco" };

export default async function PanelHome() {
  const me = await requireMe();
  return (
    <>
      <h1>Olá, {me.full_name || me.email}</h1>
      <section className={styles.card}>
        <h2>Suas lojas</h2>
        {me.memberships.length === 0 ? (
          <p className="muted">
            {me.platform_role
              ? "Você é da equipe da plataforma: as lojas ficam em Ops."
              : "Nenhuma loja vinculada à sua conta ainda."}
          </p>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Loja</th>
                <th>Papel</th>
              </tr>
            </thead>
            <tbody>
              {me.memberships.map((membership) => (
                <tr key={membership.tenant_id}>
                  <td>
                    <Link href={`/t/${membership.tenant_id}`}>{membership.tenant_slug}</Link>
                  </td>
                  <td>
                    <span className={styles.badge}>{membership.role}</span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </section>
      {me.platform_role ? (
        <section className={styles.card}>
          <h2>Plataforma</h2>
          <p>
            <Link href="/ops/tenants">Gerenciar tenants</Link>{" "}
            <span className={styles.badge}>{me.platform_role}</span>
          </p>
        </section>
      ) : null}
    </>
  );
}
