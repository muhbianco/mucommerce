import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, ApiError, requireMe } from "@/lib/panel/api";
import {
  ACCESS_MODES,
  type Domain,
  TENANT_TRANSITIONS,
  type Tenant,
  type TenantPanelContext,
} from "@/lib/panel/types";

import { setAccessMode, setFeatures, setTenantStatus } from "../../../../actions";
import styles from "../../../../panel.module.css";

export const metadata: Metadata = { title: "Tenant · Ops" };

const OK: Record<string, string> = {
  status: "Status atualizado.",
  flags: "Módulos atualizados.",
  acesso: "Acesso à vitrine atualizado.",
};

export default async function OpsTenant({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const me = await requireMe();
  if (!me.platform_role) notFound();
  const { id } = await params;
  const { ok, erro } = await searchParams;
  const path = `/ops/tenants/${encodeURIComponent(id)}`;

  let tenant: Tenant;
  try {
    tenant = await api<Tenant>(path);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) notFound();
    throw error;
  }
  const [flags, domains, context] = await Promise.all([
    api<Record<string, boolean>>(`${path}/features`),
    api<Domain[]>(`${path}/domains`),
    api<TenantPanelContext>(`/admin/tenants/${encodeURIComponent(id)}/context`),
  ]);
  const accessMode = String(context.settings.storefront?.access_mode ?? "whitelist");
  const transitions = TENANT_TRANSITIONS[tenant.status] ?? [];

  return (
    <>
      <p>
        <Link href="/ops/tenants">← Tenants</Link>
      </p>
      <h1>
        {tenant.name} <span className={styles.badge}>{tenant.status}</span>
      </h1>
      {ok && OK[ok] ? <p className={styles.ok}>{OK[ok]}</p> : null}
      {erro ? <p className={styles.error}>Não foi possível salvar ({erro}).</p> : null}

      <section className={styles.card}>
        <h2>Status</h2>
        {transitions.length === 0 ? (
          <p className="muted">Sem transições a partir de {tenant.status}.</p>
        ) : (
          <div className={styles.form}>
            {transitions.map((status) => (
              <form key={status} action={setTenantStatus}>
                <input type="hidden" name="tenant_id" value={tenant.id} />
                <input type="hidden" name="status" value={status} />
                <button type="submit" className={styles.buttonGhost}>
                  → {status}
                </button>
              </form>
            ))}
          </div>
        )}
      </section>

      <section className={styles.card}>
        <h2>Módulos</h2>
        <form action={setFeatures}>
          <input type="hidden" name="tenant_id" value={tenant.id} />
          <input type="hidden" name="keys" value={Object.keys(flags).join(",")} />
          <div className={styles.flags}>
            {Object.entries(flags).map(([key, enabled]) => (
              <label key={key}>
                <input type="checkbox" name={`flag:${key}`} defaultChecked={enabled} /> {key}
              </label>
            ))}
          </div>
          <button type="submit" className={styles.button}>
            Salvar módulos
          </button>
        </form>
      </section>

      <section className={styles.card}>
        <h2>Acesso à vitrine</h2>
        <form action={setAccessMode} className={styles.form}>
          <input type="hidden" name="tenant_id" value={tenant.id} />
          <label>
            Modo
            <select name="access_mode" defaultValue={accessMode}>
              {ACCESS_MODES.map((mode) => (
                <option key={mode} value={mode}>
                  {mode}
                </option>
              ))}
            </select>
          </label>
          <button type="submit" className={styles.button}>
            Salvar
          </button>
        </form>
      </section>

      <section className={styles.card}>
        <h2>Domínios</h2>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>Host</th>
              <th>Papel</th>
              <th>Status</th>
              <th>TLS</th>
            </tr>
          </thead>
          <tbody>
            {domains.map((domain) => (
              <tr key={domain.id}>
                <td>{domain.hostname}</td>
                <td>{domain.role}</td>
                <td>
                  <span className={styles.badge}>{domain.status}</span>
                </td>
                <td>{domain.tls_status}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </>
  );
}
