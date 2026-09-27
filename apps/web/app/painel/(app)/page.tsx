import type { Metadata } from "next";
import Link from "next/link";

import { api, requireMe } from "@/lib/panel/api";
import type { PillState } from "@/lib/panel/states";
import type { Page, TenantListItem } from "@/lib/panel/types";

import styles from "../panel.module.css";
import { EmptyState, PageHeader, Pill, Section, TableWrap } from "./t/[tenantId]/ui";

export const metadata: Metadata = { title: "Painel MuhBianco" };

const SITE_ADMIN_STORES = "https://muhbianco.com.br/admin.html#lojas";

/** Situação da loja em palavras, e a cor do selo. */
const STATUS: Record<string, { label: string; state: PillState }> = {
  active: { label: "No ar", state: "live" },
  suspended: { label: "Assinatura em atraso", state: "warn" },
  provisioning: { label: "Preparando", state: "pending" },
  draft: { label: "Em montagem", state: "pending" },
  archived: { label: "Arquivada", state: "off" },
};

const ROLE_LABEL: Record<string, string> = {
  owner: "Dono",
  admin: "Administrador",
  ops: "Operação",
  support: "Atendimento",
  plataforma: "Equipe MuhBianco",
};

export default async function PanelHome() {
  const me = await requireMe();
  // Platform staff (MuhBianco admins) can open any store; stores are created in the site admin.
  const all = me.platform_role ? await api<Page<TenantListItem>>("/ops/tenants?limit=100") : null;
  const mine = new Map(me.memberships.map((m) => [m.tenant_id, m.role]));
  const rows = all
    ? all.items.map((t) => ({
        id: t.id,
        name: t.name,
        slug: t.slug,
        status: t.status,
        host: t.primary_host,
        role: mine.get(t.id) ?? "plataforma",
      }))
    : me.memberships.map((m) => ({
        id: m.tenant_id,
        name: m.tenant_slug,
        slug: m.tenant_slug,
        status: "",
        host: null,
        role: m.role,
      }));
  const count = rows.length === 1 ? "1 loja" : `${rows.length} lojas`;

  return (
    <>
      <PageHeader
        eyebrow="Lojas"
        title={`Olá, ${me.full_name || me.email}`}
        lead={
          all
            ? "Todas as lojas da plataforma. Abra uma para entrar no painel dela; criar lojas e definir o dono, os módulos e o acesso é no admin do site."
            : "Escolha a loja que você quer abrir."
        }
        actions={
          me.platform_role ? (
            <a className={styles.buttonGhost} href={SITE_ADMIN_STORES}>
              Criar ou editar lojas
            </a>
          ) : undefined
        }
      />

      <Section
        title={all ? "Todas as lojas" : "Suas lojas"}
        description={all?.next_cursor ? `${count} · mostrando as 100 primeiras` : count}
      >
        {rows.length === 0 ? (
          <EmptyState title={all ? "Nenhuma loja cadastrada ainda" : "Nenhuma loja vinculada à sua conta ainda"}>
            {all ? "Crie a primeira no admin do site." : null}
          </EmptyState>
        ) : (
          <TableWrap>
            <table className={styles.table}>
              <thead>
                <tr>
                  <th>Loja</th>
                  {all ? <th>Endereço</th> : null}
                  {all ? <th>Situação</th> : null}
                  <th>Seu papel</th>
                </tr>
              </thead>
              <tbody>
                {rows.map((row) => {
                  const status = STATUS[row.status] ?? { label: row.status, state: "off" as const };
                  return (
                    <tr key={row.id}>
                      <td>
                        <strong>
                          <Link href={`/t/${row.id}`}>{row.name}</Link>
                        </strong>
                        {row.slug !== row.name ? (
                          <>
                            <br />
                            <small className="muted">{row.slug}</small>
                          </>
                        ) : null}
                      </td>
                      {all ? (
                        <td>
                          {row.host ? (
                            <a href={`https://${row.host}`} target="_blank" rel="noopener">
                              {row.host} ↗
                            </a>
                          ) : (
                            <span className="muted">—</span>
                          )}
                        </td>
                      ) : null}
                      {all ? (
                        <td>
                          <Pill state={status.state}>{status.label}</Pill>
                        </td>
                      ) : null}
                      <td>
                        <span className={styles.badge}>{ROLE_LABEL[row.role] ?? row.role}</span>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </TableWrap>
        )}
      </Section>
    </>
  );
}
