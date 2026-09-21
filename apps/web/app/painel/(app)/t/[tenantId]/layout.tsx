import Link from "next/link";
import type { ReactNode } from "react";

import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../panel.module.css";

// Modules show up in the menu only when the tenant has them switched on; their pages land in
// the next steps of phase 1 (products, categories, stock, settings).
export default async function TenantLayout({
  children,
  params,
}: {
  children: ReactNode;
  params: Promise<{ tenantId: string }>;
}) {
  const { tenantId } = await params;
  const context = await loadTenantContext(tenantId);
  const base = `/t/${encodeURIComponent(context.tenant_id)}`;
  return (
    <>
      <h1>{context.name}</h1>
      <nav className={styles.subnav}>
        <Link href={base}>Visão geral</Link>
      </nav>
      {children}
    </>
  );
}
