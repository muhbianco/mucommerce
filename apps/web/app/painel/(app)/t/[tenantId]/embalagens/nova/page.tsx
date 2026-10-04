import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../../../panel.module.css";
import { Flash } from "../../flash";
import { PageHeader, Section } from "../../ui";
import { createPackage } from "../actions";
import { PackageForm } from "../package-form";

export const metadata: Metadata = { title: "Nova embalagem" };

export default async function NewPackage({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  const f = context.features;
  if (!f.checkout || !tenantScopes(me, context.tenant_id).can("catalog:write")) {
    notFound();
  }
  return (
    <>
      <PageHeader
        eyebrow="Embalagens"
        title="Nova embalagem"
        lead="Cadastre do jeito que ela é: o sistema só usa a embalagem que existe de verdade na sua loja."
        actions={
          <Link href={`/t/${context.tenant_id}/embalagens`} className={styles.buttonGhost}>
            Voltar
          </Link>
        }
      />
      <Flash ok={ok} erro={erro} />
      <Section title="Embalagem" description="Depois de salvar, você vê onde os seus produtos cabem.">
        <PackageForm tenantId={context.tenant_id} action={createPackage} submitLabel="Salvar e ver onde cabe" />
      </Section>
    </>
  );
}
