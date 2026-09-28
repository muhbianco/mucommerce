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
import { EmptyState, PageHeader, Section } from "../ui";
import local from "./categorias.module.css";

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
      <option value="">Nenhuma (categoria principal)</option>
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
      <PageHeader
        eyebrow="Categorias"
        title="Organize a loja em categorias"
        lead="Agrupe os produtos para o cliente achar o que procura. Uma categoria pode ter subcategorias; cada produto entra nas categorias pela página dele."
      />
      <Flash ok={ok} erro={erro} />

      {canWrite ? (
        <Section title="Nova categoria" description="Para criar uma subcategoria, escolha em qual ela fica">
          <form action={saveCategory}>
            <input type="hidden" name="tenant_id" value={context.tenant_id} />
            <input type="hidden" name="idempotency_key" value={randomUUID()} />
            <div className={styles.fields}>
              <label className={styles.field}>
                Nome
                <input name="name" required maxLength={120} placeholder="ex.: Bolos" />
              </label>
              <label className={styles.field}>
                Dentro de
                {parentSelect(null)}
              </label>
              <label className={styles.field}>
                Ordem
                <input name="position" type="number" defaultValue={0} />
                <span className={styles.fieldHint}>Menor aparece primeiro.</span>
              </label>
            </div>
            <div className={styles.formActions}>
              <button type="submit" className={styles.button}>
                Criar categoria
              </button>
            </div>
          </form>
        </Section>
      ) : null}

      <Section
        title="Suas categorias"
        description={ordered.length ? (ordered.length === 1 ? "1 categoria" : `${ordered.length} categorias`) : undefined}
      >
        {ordered.length === 0 ? (
          <EmptyState title="Nenhuma categoria ainda">
            {canWrite
              ? "Crie a primeira acima. Depois, marque na página de cada produto as categorias dele."
              : "Quando a loja tiver categorias, elas aparecem aqui."}
          </EmptyState>
        ) : (
          <>
            {canWrite ? (
              <p className={local.intro}>
                Mude o que precisar e clique em Salvar na mesma linha. O endereço é o fim do link da categoria na
                loja (<code>/loja/categoria/endereço</code>).
              </p>
            ) : null}
            <ul className={`${styles.rows} ${local.list}`}>
              {ordered.map((category) => {
                const child = Boolean(category.parent_id);
                return (
                  <li key={category.id} className={child ? `${styles.row} ${local.child}` : styles.row}>
                    <form action={saveCategory} className={local.edit}>
                      <input type="hidden" name="tenant_id" value={context.tenant_id} />
                      <input type="hidden" name="category_id" value={category.id} />
                      <fieldset disabled={!canWrite} className={local.fieldset}>
                        <div className={local.fields}>
                          <label className={styles.field}>
                            {child ? "Subcategoria" : "Categoria"}
                            <input name="name" required maxLength={120} defaultValue={category.name} />
                          </label>
                          <label className={styles.field}>
                            Endereço
                            <input name="slug" maxLength={160} defaultValue={category.slug} />
                          </label>
                          <label className={styles.field}>
                            Dentro de
                            {parentSelect(category)}
                          </label>
                          <label className={styles.field}>
                            Ordem
                            <input name="position" type="number" defaultValue={category.position} />
                          </label>
                        </div>
                      </fieldset>
                      {canWrite ? (
                        <button type="submit" className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
                          Salvar
                        </button>
                      ) : null}
                    </form>
                    {canWrite ? (
                      <form action={archiveCategory} className={local.archive}>
                        <input type="hidden" name="tenant_id" value={context.tenant_id} />
                        <input type="hidden" name="category_id" value={category.id} />
                        <button type="submit" className={`${styles.buttonDanger} ${styles.buttonSmall}`}>
                          Arquivar
                        </button>
                      </form>
                    ) : null}
                  </li>
                );
              })}
            </ul>
          </>
        )}
      </Section>
    </>
  );
}
