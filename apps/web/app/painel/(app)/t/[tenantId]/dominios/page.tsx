import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../../panel.module.css";
import { CopyButton } from "../copy-button";
import { Flash } from "../flash";
import { addDomain, disableDomain, makePrimary, verifyDomain } from "./actions";

export const metadata: Metadata = { title: "Endereços da loja" };

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
  purpose: string;
  role: string;
  status: string;
  verified_at: string | null;
  last_error: string | null;
  instructions: DnsInstructions | null;
}

/** Situação em palavras de lojista, e a cor do selo (live | pending | warn | off). */
const STATUS: Record<string, { label: string; state: string }> = {
  active: { label: "No ar", state: "live" },
  pending_dns: { label: "Esperando o DNS", state: "pending" },
  verifying: { label: "Conferindo", state: "pending" },
  verified: { label: "Falta apontar", state: "pending" },
  failed: { label: "Não deu certo", state: "warn" },
  disabled: { label: "Desativado", state: "off" },
};

/** O que a pessoa precisa criar na zona dela, em linguagem de painel de DNS. */
function records(dns: DnsInstructions, hostname: string): { tipo: string; nome: string; valor: string }[] {
  const posse = { tipo: "TXT", nome: dns.txt_name, valor: dns.txt_value };
  if (!dns.apex) return [posse, { tipo: "CNAME", nome: hostname, valor: dns.cname_target }];
  return [posse, ...dns.a_records.map((ip) => ({ tipo: "A", nome: hostname, valor: ip }))];
}

// Sufixos de segundo nível comuns no Brasil: em x.com.br, o domínio da pessoa é x.com.br.
const SECOND_LEVEL = new Set(["com.br", "net.br", "org.br", "art.br", "blog.br", "eco.br", "ind.br", "srv.br", "tur.br"]);

/** O domínio que a pessoa comprou (a zona de DNS dela), a partir do endereço cadastrado. */
function zoneOf(hostname: string): string {
  const labels = hostname.split(".");
  const size = SECOND_LEVEL.has(labels.slice(-2).join(".")) ? 3 : 2;
  return labels.slice(-size).join(".");
}

/** Como a maioria dos painéis de DNS pede o nome: só a parte antes do domínio ("@" na raiz). */
function relativeName(name: string, zone: string): string {
  if (name === zone) return "@";
  return name.endsWith(`.${zone}`) ? name.slice(0, -(zone.length + 1)) : name;
}

function StatusPill({ status }: { status: string }) {
  const view = STATUS[status] ?? { label: status, state: "off" };
  return (
    <span className={styles.pill} data-state={view.state}>
      {view.label}
    </span>
  );
}

/** Um botão que dispara uma ação do servidor para um domínio. */
function DomainAction({
  action,
  tenantId,
  domainId,
  className,
  children,
}: {
  action: (form: FormData) => Promise<void>;
  tenantId: string;
  domainId: string;
  className: string;
  children: ReactNode;
}) {
  return (
    <form action={action}>
      <input type="hidden" name="tenant_id" value={tenantId} />
      <input type="hidden" name="domain_id" value={domainId} />
      <button type="submit" className={className}>
        {children}
      </button>
    </form>
  );
}

function DnsSteps({ domain, tenantId }: { domain: Domain; tenantId: string }) {
  if (!domain.instructions) return null;
  const zone = zoneOf(domain.hostname);
  return (
    <ol className={styles.steps}>
      <li>
        <p>
          <strong>Crie estes registros</strong> no painel de DNS de <strong>{zone}</strong> (onde você
          comprou o domínio: Registro.br, GoDaddy, Hostinger, Cloudflare…).
        </p>
        <div className={styles.records}>
          {records(domain.instructions, domain.hostname).map((row) => (
            <div key={`${row.tipo}-${row.valor}`} className={styles.record}>
              <span className={styles.badge}>{row.tipo}</span>
              <span className={styles.recordLabel}>Nome</span>
              <span className={styles.copyValue}>
                <code title={row.nome}>{relativeName(row.nome, zone)}</code>
                <CopyButton value={relativeName(row.nome, zone)} />
              </span>
              <span className={styles.recordLabel}>Valor</span>
              <span className={styles.copyValue}>
                <code>{row.valor}</code>
                <CopyButton value={row.valor} />
              </span>
            </div>
          ))}
        </div>
        <p className={styles.hint}>
          No campo <strong>Nome</strong> vai só a parte antes de <code>{zone}</code>, como mostrado
          acima; se o seu painel pedir o nome completo, acrescente <code>.{zone}</code> no fim.
        </p>
        {domain.instructions.apex ? (
          <p className={styles.hint}>
            Domínio raiz usa registro A. Se preferir não mexer na raiz, cadastre{" "}
            <code>loja.{domain.hostname}</code>: ele usa CNAME, que é mais simples.
          </p>
        ) : null}
      </li>
      <li>
        <p>
          <strong>Espere a conferência.</strong> Olhamos sozinhos a cada 5 minutos; o DNS costuma levar
          de alguns minutos a algumas horas para valer.
        </p>
        {domain.last_error ? <p className={styles.note}>Última conferência: {domain.last_error}</p> : null}
        <DomainAction
          action={verifyDomain}
          tenantId={tenantId}
          domainId={domain.id}
          className={`${styles.buttonGhost} ${styles.buttonSmall}`}
        >
          Conferir agora
        </DomainAction>
      </li>
      <li>
        <p>
          <strong>Quando ficar &ldquo;No ar&rdquo;</strong>, torne-o o endereço principal para ele
          aparecer nos links e no Google.
        </p>
      </li>
    </ol>
  );
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
  const id = context.tenant_id;

  // Só a vitrine nesta lista; painel e Chatwoot têm os endereços deles (e a mesma rota da API).
  const store = domains.filter((d) => d.purpose === "storefront");
  const live = store
    .filter((d) => d.status !== "disabled")
    .sort((a, b) => Number(b.role === "primary") - Number(a.role === "primary"));
  const disabled = store.filter((d) => d.status === "disabled");
  const panels = domains.filter((d) => d.purpose === "panel" && d.status === "active");

  return (
    <>
      <header className={styles.pageHead}>
        <span className={styles.eyebrow}>Endereços</span>
        <h2>Onde a sua loja atende</h2>
        <p className={styles.lead}>
          A loja responde em todos os endereços abaixo. O <strong>principal</strong> é o que aparece nos
          links e no Google; os outros levam para ele.
        </p>
      </header>
      <Flash ok={ok} erro={erro} />

      <section className={styles.card}>
        <div className={styles.sectionHead}>
          <h3>Vitrine</h3>
          <p>{live.length === 1 ? "1 endereço" : `${live.length} endereços`}</p>
        </div>
        <div className={styles.rows}>
          {live.map((domain) => {
            const platform = domain.kind === "platform_subdomain";
            const active = domain.status === "active";
            const primary = domain.role === "primary";
            return (
              <div key={domain.id} className={styles.row}>
                <div className={styles.rowMain}>
                  <div className={styles.host}>
                    {active ? (
                      <a href={`https://${domain.hostname}`} target="_blank" rel="noopener">
                        {domain.hostname} ↗
                      </a>
                    ) : (
                      domain.hostname
                    )}
                  </div>
                  <p className={styles.rowSub}>
                    {platform ? "Endereço gratuito da MuhBianco · fica com a loja para sempre" : "Seu domínio"}
                  </p>
                </div>
                <div className={styles.rowBadges}>
                  <StatusPill status={domain.status} />
                  {primary ? <span className={styles.tag}>Principal</span> : null}
                </div>
                <div className={styles.rowActions}>
                  {active && !primary ? (
                    <DomainAction
                      action={makePrimary}
                      tenantId={id}
                      domainId={domain.id}
                      className={`${styles.buttonGhost} ${styles.buttonSmall}`}
                    >
                      Tornar principal
                    </DomainAction>
                  ) : null}
                  {!platform && !primary ? (
                    <DomainAction
                      action={disableDomain}
                      tenantId={id}
                      domainId={domain.id}
                      className={`${styles.buttonDanger} ${styles.buttonSmall}`}
                    >
                      Desativar
                    </DomainAction>
                  ) : null}
                </div>
                {!active ? (
                  <div className={styles.rowDetail}>
                    {domain.status === "failed" && domain.last_error ? (
                      <p className={styles.error}>{domain.last_error}</p>
                    ) : null}
                    <DnsSteps domain={domain} tenantId={id} />
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      </section>

      {panels.length ? (
        <section className={styles.card}>
          <div className={styles.sectionHead}>
            <h3>Painel da loja</h3>
            <p>Onde você e a sua equipe entram</p>
          </div>
          <div className={styles.rows}>
            {panels.map((domain) => (
              <div key={domain.id} className={styles.row}>
                <div className={styles.rowMain}>
                  <div className={styles.host}>{domain.hostname}</div>
                  <p className={styles.rowSub}>Guarde nos favoritos: é o endereço deste painel.</p>
                </div>
                <div className={styles.rowActions}>
                  <CopyButton value={`https://${domain.hostname}`} label="Copiar link" />
                </div>
              </div>
            ))}
          </div>
        </section>
      ) : null}

      <section className={styles.card}>
        <div className={styles.sectionHead}>
          <h3>Conectar um domínio seu</h3>
          <p>Opcional · o endereço da MuhBianco continua funcionando</p>
        </div>
        <form action={addDomain} className={styles.form}>
          <input type="hidden" name="tenant_id" value={id} />
          <label className={styles.formGrow}>
            Domínio
            <input name="hostname" maxLength={253} placeholder="loja.minhaempresa.com.br" autoComplete="off" required />
          </label>
          <button type="submit" className={styles.button}>
            Conectar domínio
          </button>
        </form>
        <p className={styles.hint}>
          Prefira um subdomínio (<code>loja.minhaempresa.com.br</code>): é o jeito mais simples. O domínio
          continua seu: registro e renovação ficam com você, e dá para desativar aqui quando quiser.
        </p>
      </section>

      {disabled.length ? (
        <details className={styles.card}>
          <summary>Endereços desativados ({disabled.length})</summary>
          <div className={styles.rows} style={{ marginTop: "1rem" }}>
            {disabled.map((domain) => (
              <div key={domain.id} className={styles.row}>
                <div className={styles.rowMain}>
                  <div className={styles.host}>{domain.hostname}</div>
                  <p className={styles.rowSub}>Continua reservado para a sua loja.</p>
                </div>
                <StatusPill status={domain.status} />
              </div>
            ))}
          </div>
        </details>
      ) : null}
    </>
  );
}
