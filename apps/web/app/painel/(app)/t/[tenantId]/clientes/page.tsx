import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Page } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { setCustomerAccess } from "../actions";
import { Flash } from "../flash";

export const metadata: Metadata = { title: "Clientes" };

interface CustomerAccess {
  customer_id: string;
  name: string | null;
  email: string | null;
  phone_e164: string | null;
  phone_verified: boolean;
  status: "pending" | "approved" | "blocked" | "revoked";
  source: string;
  requested_at: string | null;
  request_message: string | null;
  status_changed_at: string | null;
  note: string | null;
  created_at: string;
}

const TABS: [string, string][] = [
  ["pending", "Pendentes"],
  ["approved", "Aprovados"],
  ["blocked", "Bloqueados"],
  ["revoked", "Revogados"],
  ["", "Todos"],
];
const STATUS_LABEL: Record<CustomerAccess["status"], string> = {
  pending: "pendente",
  approved: "aprovado",
  blocked: "bloqueado",
  revoked: "revogado",
};
// Same transitions as api-commerce (app/customers/access_service.py).
const ACTIONS: Record<CustomerAccess["status"], ("approved" | "blocked" | "revoked")[]> = {
  pending: ["approved", "blocked"],
  approved: ["revoked", "blocked"],
  revoked: ["approved", "blocked"],
  blocked: ["approved", "revoked"],
};
const ACTION_LABEL = { approved: "Liberar", blocked: "Bloquear", revoked: "Revogar" } as const;

function when(value: string | null): string {
  return value ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short" }) : "—";
}

export default async function Customers({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ status?: string; q?: string; cursor?: string; ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const query = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  const scopes = tenantScopes(me, context.tenant_id);
  if (!scopes.can("customers:read")) notFound();
  const canDecide = scopes.can("customers:approve");
  const base = `/t/${encodeURIComponent(context.tenant_id)}/clientes`;

  const status = TABS.some(([value]) => value === query.status) ? (query.status ?? "") : "pending";
  const search = new URLSearchParams({ limit: "25" });
  if (status) search.set("status", status);
  if (query.q && query.q.trim().length >= 2) search.set("q", query.q.trim().slice(0, 100));
  if (query.cursor) search.set("cursor", query.cursor.slice(0, 256));
  const page = await api<Page<CustomerAccess>>(
    `/admin/tenants/${context.tenant_id}/customers?${search.toString()}`,
  );
  const listPath = (extra: Record<string, string> = {}) => {
    const params = new URLSearchParams({ status, ...(query.q ? { q: query.q } : {}), ...extra });
    return `${base}?${params.toString()}`;
  };

  return (
    <>
      <Flash ok={query.ok} erro={query.erro} />
      <section className={styles.card}>
        <h2>Clientes</h2>
        <p className="muted">
          {(context.settings.storefront?.access_mode ?? "whitelist") === "whitelist"
            ? "A vitrine desta loja só mostra o catálogo para clientes liberados aqui."
            : "Esta loja não usa lista de aprovação; bloquear ainda impede o acesso."}
        </p>
        <nav className={styles.subnav} aria-label="Situação">
          {TABS.map(([value, label]) => (
            <Link key={value || "todos"} href={`${base}?status=${value}`} aria-current={value === status ? "page" : undefined}>
              {label}
            </Link>
          ))}
        </nav>
        <form className={styles.form} method="get">
          <input type="hidden" name="status" value={status} />
          <label>
            Buscar
            <input name="q" defaultValue={query.q ?? ""} placeholder="nome, e-mail ou telefone" minLength={2} />
          </label>
          <button type="submit" className={styles.buttonGhost}>
            Buscar
          </button>
        </form>
        {page.items.length === 0 ? (
          <p className="muted">Nenhum cliente aqui.</p>
        ) : (
          <table className={styles.table}>
            <thead>
              <tr>
                <th>Cliente</th>
                <th>Situação</th>
                <th>Pedido</th>
                <th>Ações</th>
              </tr>
            </thead>
            <tbody>
              {page.items.map((customer) => (
                <tr key={customer.customer_id}>
                  <td>
                    <strong>{customer.name ?? "Sem nome"}</strong>
                    <br />
                    <small>{customer.email ?? "—"}</small>
                    {customer.phone_e164 ? (
                      <>
                        <br />
                        <small>
                          {customer.phone_e164} {customer.phone_verified ? "✓" : "(não confirmado)"}
                        </small>
                      </>
                    ) : null}
                  </td>
                  <td>
                    <span className={styles.badge}>{STATUS_LABEL[customer.status]}</span>
                    {customer.note ? (
                      <>
                        <br />
                        <small>{customer.note}</small>
                      </>
                    ) : null}
                  </td>
                  <td>
                    <small>{when(customer.requested_at)}</small>
                    {customer.request_message ? (
                      <>
                        <br />
                        <small>“{customer.request_message}”</small>
                      </>
                    ) : null}
                  </td>
                  <td>
                    {canDecide
                      ? ACTIONS[customer.status].map((action) => (
                          <form key={action} action={setCustomerAccess} className={styles.form}>
                            <input type="hidden" name="tenant_id" value={context.tenant_id} />
                            <input type="hidden" name="customer_id" value={customer.customer_id} />
                            <input type="hidden" name="status" value={action} />
                            <input type="hidden" name="back" value={listPath()} />
                            {action === "approved" ? null : (
                              <input name="note" required maxLength={500} placeholder="Motivo" aria-label="Motivo" />
                            )}
                            <button type="submit" className={action === "approved" ? styles.button : styles.buttonGhost}>
                              {ACTION_LABEL[action]}
                            </button>
                          </form>
                        ))
                      : "—"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {page.next_cursor ? <Link href={listPath({ cursor: page.next_cursor })}>Próxima página</Link> : null}
      </section>
    </>
  );
}
