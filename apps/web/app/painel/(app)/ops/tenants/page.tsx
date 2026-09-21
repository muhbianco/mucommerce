import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import type { Page, Tenant } from "@/lib/panel/types";

import { createTenant } from "../../../actions";
import styles from "../../../panel.module.css";

export const metadata: Metadata = { title: "Tenants · Ops" };

const ERRORS: Record<string, string> = {
  conflict: "Já existe um tenant com esse slug.",
  validation_error: "Slug ou nome inválido (slug: minúsculas, números e hífen).",
  idempotency_in_progress: "Essa criação ainda está em andamento.",
};

export default async function OpsTenants({
  searchParams,
}: {
  searchParams: Promise<{ cursor?: string; erro?: string }>;
}) {
  const me = await requireMe();
  if (!me.platform_role) notFound();
  const { cursor, erro } = await searchParams;
  const query = new URLSearchParams({ limit: "25", ...(cursor ? { cursor } : {}) });
  const page = await api<Page<Tenant>>(`/ops/tenants?${query}`);
  return (
    <>
      <h1>Tenants</h1>
      {erro ? <p className={styles.error}>{ERRORS[erro] ?? `Erro: ${erro}`}</p> : null}
      <section className={styles.card}>
        <h2>Novo tenant</h2>
        <form action={createTenant} className={styles.form}>
          <input type="hidden" name="idempotency_key" value={crypto.randomUUID()} />
          <label>
            Slug
            <input name="slug" required minLength={3} maxLength={63} pattern="[a-z0-9][a-z0-9-]{1,61}[a-z0-9]" />
          </label>
          <label>
            Nome
            <input name="name" required minLength={2} maxLength={160} />
          </label>
          <button type="submit" className={styles.button}>
            Criar (rascunho)
          </button>
        </form>
      </section>
      <section className={styles.card}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Slug</th>
              <th>Nome</th>
              <th>Status</th>
              <th>Plano</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map((tenant) => (
              <tr key={tenant.id}>
                <td>
                  <Link href={`/ops/tenants/${tenant.id}`}>{tenant.slug}</Link>
                </td>
                <td>{tenant.name}</td>
                <td>
                  <span className={styles.badge}>{tenant.status}</span>
                </td>
                <td>{tenant.plan}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {page.next_cursor ? (
          <p>
            <Link href={`/ops/tenants?cursor=${encodeURIComponent(page.next_cursor)}`}>Mais antigos →</Link>
          </p>
        ) : null}
      </section>
    </>
  );
}
