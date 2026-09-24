import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { addDomain, disableDomain, makePrimary, verifyDomain } from "./actions";

export const metadata: Metadata = { title: "Endereço da loja" };

interface DnsInstructions {
  txt_name: string;
  txt_value: string;
  a_records: string[];
  cname_target: string;
  apex: boolean;
}

interface Domain {
  id: string;
  hostname: string;
  kind: string;
  role: string;
  status: string;
  verified_at: string | null;
  last_error: string | null;
  instructions: DnsInstructions | null;
}

const STATUS: Record<string, string> = {
  pending_dns: "esperando o DNS",
  verifying: "conferindo",
  verified: "posse confirmada, falta apontar",
  active: "no ar",
  failed: "falhou",
  disabled: "desativado",
};

/** O que a pessoa precisa criar na zona dela, em linguagem de painel de DNS. */
function records(dns: DnsInstructions, hostname: string): { tipo: string; nome: string; valor: string }[] {
  const posse = { tipo: "TXT", nome: dns.txt_name, valor: dns.txt_value };
  if (!dns.apex) return [posse, { tipo: "CNAME", nome: hostname, valor: dns.cname_target }];
  return [posse, ...dns.a_records.map((ip) => ({ tipo: "A", nome: hostname, valor: ip }))];
}

export default async function Domains({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!tenantScopes(me, context.tenant_id).can("domains:write")) notFound();
  const domains = await api<Domain[]>(`/admin/tenants/${tenantId}/domains`);
  const tenantField = <input type="hidden" name="tenant_id" value={context.tenant_id} />;
  const platform = domains.find((domain) => domain.kind === "platform_subdomain");
  const customs = domains.filter((domain) => domain.kind !== "platform_subdomain");

  return (
    <>
      <h2>Endereço da loja</h2>
      <Flash ok={ok} erro={erro} />
      <p className="muted">
        A loja já responde no endereço da MuhBianco. Se você tem um domínio seu, aponte o DNS dele
        para cá: enquanto não entrar no ar, a loja continua funcionando no endereço abaixo.
      </p>

      {platform ? (
        <section className={styles.card}>
          <h2>Endereço da MuhBianco</h2>
          <p>
            <a href={`https://${platform.hostname}`} target="_blank" rel="noopener">
              {platform.hostname}
            </a>{" "}
            — {STATUS[platform.status] ?? platform.status}
            {platform.role === "primary" ? " · principal" : ""}
          </p>
          <p className="muted">Este endereço é seu enquanto a loja existir e não pode ser removido.</p>
        </section>
      ) : null}

      {customs.map((domain) => (
        <section key={domain.id} className={styles.card}>
          <h2>
            {domain.hostname} — {STATUS[domain.status] ?? domain.status}
            {domain.role === "primary" ? " · principal" : ""}
          </h2>
          {domain.last_error ? <p className={styles.error}>{domain.last_error}</p> : null}

          {domain.status !== "active" && domain.instructions ? (
            <>
              <p className="muted">
                Crie estes registros no painel de DNS de {domain.hostname.split(".").slice(-2).join(".")}
                . A conferência roda sozinha a cada 5 minutos.
              </p>
              <div className="tx-table-wrap">
                <table className="tx-table">
                  <thead>
                    <tr>
                      <th>Tipo</th>
                      <th>Nome</th>
                      <th>Valor</th>
                    </tr>
                  </thead>
                  <tbody>
                    {records(domain.instructions, domain.hostname).map((row) => (
                      <tr key={`${row.tipo}-${row.valor}`}>
                        <td>{row.tipo}</td>
                        <td>
                          <code>{row.nome}</code>
                        </td>
                        <td>
                          <code>{row.valor}</code>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              {domain.instructions.apex ? (
                <p className="muted">
                  Domínio raiz usa registro A. Se preferir não mexer na raiz, cadastre
                  <code> loja.{domain.hostname}</code> e aponte por CNAME.
                </p>
              ) : null}
            </>
          ) : null}

          <div className={styles.form}>
            {domain.status !== "disabled" ? (
              <form action={verifyDomain}>
                {tenantField}
                <input type="hidden" name="domain_id" value={domain.id} />
                <button type="submit">Conferir agora</button>
              </form>
            ) : null}
            {domain.status === "active" && domain.role !== "primary" ? (
              <form action={makePrimary}>
                {tenantField}
                <input type="hidden" name="domain_id" value={domain.id} />
                <button type="submit">Usar como endereço principal</button>
              </form>
            ) : null}
            {domain.status !== "disabled" && domain.role !== "primary" ? (
              <form action={disableDomain}>
                {tenantField}
                <input type="hidden" name="domain_id" value={domain.id} />
                <button type="submit">Desativar</button>
              </form>
            ) : null}
          </div>
        </section>
      ))}

      <section className={styles.card}>
        <h2>Usar um domínio meu</h2>
        <form action={addDomain} className={styles.form}>
          {tenantField}
          <label>
            Domínio
            <input
              name="hostname"
              maxLength={253}
              placeholder="loja.minhaempresa.com.br"
              autoComplete="off"
            />
          </label>
          <button type="submit">Adicionar</button>
        </form>
        <p className="muted">
          Pode ser um subdomínio (<code>loja.minhaempresa.com.br</code>) ou o domínio raiz
          (<code>minhaempresa.com.br</code>). O domínio é seu: quem paga o registro e a renovação é
          você, e dá para desativar aqui quando quiser.
        </p>
      </section>
    </>
  );
}
