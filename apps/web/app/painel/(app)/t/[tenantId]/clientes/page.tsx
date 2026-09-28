import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import type { PillState } from "@/lib/panel/states";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Page } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { setCustomerAccess } from "../actions";
import { Flash } from "../flash";
import { EmptyState, PageHeader, Pill, Section, TableWrap } from "../ui";
import local from "./clientes.module.css";

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
const STATUS_STATE: Record<CustomerAccess["status"], PillState> = {
  pending: "pending",
  approved: "live",
  blocked: "warn",
  revoked: "off",
};
// Same transitions as api-commerce (app/customers/access_service.py).
const ACTIONS: Record<CustomerAccess["status"], ("approved" | "blocked" | "revoked")[]> = {
  pending: ["approved", "blocked"],
  approved: ["revoked", "blocked"],
  revoked: ["approved", "blocked"],
  blocked: ["approved", "revoked"],
};
const ACTION_LABEL = { approved: "Liberar", blocked: "Bloquear", revoked: "Revogar" } as const;
const NOTE_LABEL = { blocked: "Motivo do bloqueio", revoked: "Motivo para revogar" } as const;

interface TabView {
  title: string;
  empty: string;
  emptyText: string;
}

const ALL_VIEW: TabView = {
  title: "Todos os clientes",
  empty: "Nenhum cliente ainda",
  emptyText: "Quem se cadastrar na loja aparece aqui.",
};

/** Título de cada filtro e o que dizer quando ele está vazio. */
const TAB_VIEW: Record<string, TabView> = {
  pending: {
    title: "Esperando a sua resposta",
    empty: "Ninguém esperando resposta",
    emptyText: "Quando alguém pedir acesso à loja, o pedido aparece aqui para você liberar ou bloquear.",
  },
  approved: {
    title: "Com acesso liberado",
    empty: "Ninguém liberado ainda",
    emptyText: "Quem você liberar aparece aqui; dá para revogar ou bloquear quando quiser.",
  },
  blocked: {
    title: "Bloqueados",
    empty: "Ninguém bloqueado",
    emptyText: "Quem você bloquear não entra na loja e aparece aqui.",
  },
  revoked: {
    title: "Com acesso revogado",
    empty: "Nenhum acesso revogado",
    emptyText: "Quem perder o acesso aparece aqui; dá para liberar de novo.",
  },
  "": ALL_VIEW,
};

/** Data e hora no fuso da loja (o servidor roda em UTC). */
function when(value: string | null, timeZone: string): string {
  return value
    ? new Date(value).toLocaleString("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone })
    : "—";
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
  const term = query.q?.trim() ?? "";
  const searching = term.length >= 2;
  if (searching) search.set("q", term.slice(0, 100));
  if (query.cursor) search.set("cursor", query.cursor.slice(0, 256));
  const page = await api<Page<CustomerAccess>>(
    `/admin/tenants/${context.tenant_id}/customers?${search.toString()}`,
  );
  const listPath = (extra: Record<string, string> = {}) => {
    const params = new URLSearchParams({ status, ...(query.q ? { q: query.q } : {}), ...extra });
    return `${base}?${params.toString()}`;
  };
  const whitelist = (context.settings.storefront?.access_mode ?? "whitelist") === "whitelist";
  const view = TAB_VIEW[status] ?? ALL_VIEW;
  // Uma ação coral por seção: na linha, liberar é contorno e bloquear/revogar fica discreto.
  const ghostSmall = `${styles.buttonGhost} ${styles.buttonSmall}`;
  const undoButton = `${styles.buttonDanger} ${styles.buttonSmall}`;

  return (
    <>
      <PageHeader
        eyebrow="Clientes"
        title="Quem tem acesso à sua loja"
        lead={
          whitelist
            ? "A sua vitrine só mostra o catálogo para clientes liberados aqui. Libere quem pediu acesso e bloqueie quem não deve entrar."
            : "A sua loja não usa lista de aprovação, mas bloquear um cliente aqui ainda impede o acesso dele."
        }
      />
      <Flash ok={query.ok} erro={query.erro} />

      <Section
        title={view.title}
        description={canDecide ? undefined : "O seu papel permite ver a lista, mas não liberar nem bloquear."}
      >
        <nav className={local.filters} aria-label="Situação">
          {TABS.map(([value, label]) => (
            <Link
              key={value || "todos"}
              href={`${base}?status=${value}`}
              className={local.chip}
              aria-current={value === status ? "page" : undefined}
            >
              {label}
            </Link>
          ))}
        </nav>
        <form className={styles.toolbar} method="get">
          <input type="hidden" name="status" value={status} />
          <label className={styles.formGrow}>
            Buscar
            <input name="q" defaultValue={query.q ?? ""} placeholder="nome, e-mail ou telefone" minLength={2} />
          </label>
          <button type="submit" className={styles.buttonGhost}>
            Buscar
          </button>
        </form>

        {page.items.length === 0 ? (
          searching ? (
            <EmptyState title="Ninguém encontrado">
              Nenhum resultado para &ldquo;{term}&rdquo; neste filtro. Confira a grafia ou procure em
              &ldquo;Todos&rdquo;.
            </EmptyState>
          ) : (
            <EmptyState title={view.empty}>{view.emptyText}</EmptyState>
          )
        ) : (
          <TableWrap>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Cliente</th>
                  <th>Situação</th>
                  <th>Pediu acesso</th>
                  {canDecide ? <th>Ações</th> : null}
                </tr>
              </thead>
              <tbody>
                {page.items.map((customer) => (
                  <tr key={customer.customer_id}>
                    <td>
                      <div className={local.person}>
                        <strong>{customer.name ?? "Sem nome"}</strong>
                        <span className={local.sub}>{customer.email ?? "—"}</span>
                        {customer.phone_e164 ? (
                          <span className={local.sub}>
                            {customer.phone_e164} ·{" "}
                            {customer.phone_verified ? (
                              <span className={local.verified}>✓ confirmado</span>
                            ) : (
                              "não confirmado"
                            )}
                          </span>
                        ) : null}
                      </div>
                    </td>
                    <td>
                      <Pill state={STATUS_STATE[customer.status]}>{STATUS_LABEL[customer.status]}</Pill>
                      {customer.note ? <p className={local.note}>{customer.note}</p> : null}
                    </td>
                    <td>
                      <span className={local.sub}>{when(customer.requested_at, context.timezone)}</span>
                      {customer.request_message ? (
                        <p className={local.message}>&ldquo;{customer.request_message}&rdquo;</p>
                      ) : null}
                    </td>
                    {canDecide ? (
                      <td>
                        <div className={local.decisions}>
                          {ACTIONS[customer.status].map((action) => (
                            <form key={action} action={setCustomerAccess} className={local.decision}>
                              <input type="hidden" name="tenant_id" value={context.tenant_id} />
                              <input type="hidden" name="customer_id" value={customer.customer_id} />
                              <input type="hidden" name="status" value={action} />
                              <input type="hidden" name="back" value={listPath()} />
                              {action === "approved" ? null : (
                                <input
                                  name="note"
                                  required
                                  maxLength={500}
                                  placeholder="Motivo"
                                  aria-label={NOTE_LABEL[action]}
                                />
                              )}
                              <button type="submit" className={action === "approved" ? ghostSmall : undoButton}>
                                {ACTION_LABEL[action]}
                              </button>
                            </form>
                          ))}
                        </div>
                      </td>
                    ) : null}
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
        )}

        {query.cursor || page.next_cursor ? (
          <div className={styles.pager}>
            {query.cursor ? (
              <Link href={listPath()} className={ghostSmall}>
                Primeira página
              </Link>
            ) : null}
            {page.next_cursor ? (
              <Link href={listPath({ cursor: page.next_cursor })} className={ghostSmall}>
                Próxima página →
              </Link>
            ) : null}
          </div>
        ) : null}
      </Section>
    </>
  );
}
