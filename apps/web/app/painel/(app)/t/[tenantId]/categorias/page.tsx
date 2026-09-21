import { randomUUID } from "node:crypto";

import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Category } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { archiveCategory, saveCategory } from "../actions";
import { Flash } from "../flash";

export const metadata: Metadata = { title: "Categorias" };

export default async function Categories({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!context.features.catalog) notFound();
  const canWrite = tenantScopes(me, context.tenant_id).can("catalog:write");
  const categories = await api<Category[]>(`/admin/tenants/${context.tenant_id}/categories`);
  const roots = categories.filter((c) => !c.parent_id);
  // Tree order: each root followed by its children (two levels at most).
  const ordered = roots.flatMap((root) => [root, ...categories.filter((c) => c.parent_id === root.id)]);

  const parentSelect = (current: Category | null) => (
    <select name="parent_id" defaultValue={current?.parent_id ?? ""}>
      <option value="">(topo)</option>
      {roots
        .filter((root) => root.id !== current?.id)
        .map((root) => (
          <option key={root.id} value={root.id}>
            {root.name}
          </option>
        ))}
    </select>
  );

  return (
    <>
      <Flash ok={ok} erro={erro} />
      {canWrite ? (
        <section className={styles.card}>
          <h2>Nova categoria</h2>
          <form action={saveCategory} className={styles.form}>
            <input type="hidden" name="tenant_id" value={context.tenant_id} />
            <input type="hidden" name="idempotency_key" value={randomUUID()} />
            <label>
              Nome
              <input name="name" required maxLength={120} />
            </label>
            <label>
              Dentro de
              {parentSelect(null)}
            </label>
            <label>
              Ordem
              <input name="position" type="number" defaultValue={0} style={{ width: "5rem" }} />
            </label>
            <button type="submit" className={styles.button}>
              Criar
            </button>
          </form>
        </section>
      ) : null}
      <section className={styles.card}>
        <h2>Categorias</h2>
        {ordered.length === 0 ? <p>Nenhuma categoria.</p> : null}
        {ordered.map((category) => (
          <div key={category.id} className={styles.form} style={{ marginBottom: "0.75rem" }}>
            <form action={saveCategory} className={styles.form}>
              <input type="hidden" name="tenant_id" value={context.tenant_id} />
              <input type="hidden" name="category_id" value={category.id} />
              <label>
                {category.parent_id ? "↳ Nome" : "Nome"}
                <input name="name" required maxLength={120} defaultValue={category.name} disabled={!canWrite} />
              </label>
              <label>
                Slug
                <input name="slug" maxLength={160} defaultValue={category.slug} disabled={!canWrite} />
              </label>
              <label>
                Dentro de
                {parentSelect(category)}
              </label>
              <label>
                Ordem
                <input name="position" type="number" defaultValue={category.position} style={{ width: "5rem" }} />
              </label>
              {canWrite ? (
                <button type="submit" className={styles.buttonGhost}>
                  Salvar
                </button>
              ) : null}
            </form>
            {canWrite ? (
              <form action={archiveCategory}>
                <input type="hidden" name="tenant_id" value={context.tenant_id} />
                <input type="hidden" name="category_id" value={category.id} />
                <button type="submit" className={styles.buttonGhost}>
                  Arquivar
                </button>
              </form>
            ) : null}
          </div>
        ))}
      </section>
    </>
  );
}
