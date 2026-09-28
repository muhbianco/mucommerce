import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { isStoreMember, tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { PageHeader, Pill, Section } from "../ui";
import { setAccessMode, toggleModule } from "./actions";

export const metadata: Metadata = { title: "Módulos da loja" };

interface Module {
  key: string;
  label: string;
  summary: string;
  where: string | null;
  self_service: boolean;
  locked_reason: string | null;
  requires: string[];
  enabled: boolean;
  dependents: string[];
}

const ACCESS: { value: string; label: string; hint: string }[] = [
  { value: "public", label: "Qualquer pessoa", hint: "A vitrine é pública; quem quiser compra." },
  {
    value: "login_required",
    label: "Só quem entrar",
    hint: "Precisa ter conta para ver preço e comprar.",
  },
  {
    value: "whitelist",
    label: "Só quem você aprovar",
    hint: "Cada cliente entra na fila e você libera na tela de Clientes.",
  },
];

function ModuleRow({
  module: item,
  tenantId,
  labels,
  readOnly,
}: {
  module: Module;
  tenantId: string;
  labels: Record<string, string>;
  readOnly: boolean;
}) {
  const blocked = item.requires.filter((key) => !labels[`on:${key}`]);
  const held = item.dependents;
  return (
    <div className={styles.row}>
      <div className={styles.rowMain}>
        <strong>{item.label}</strong>
        <p className={styles.rowSub}>{item.summary}</p>
        {item.enabled && item.where ? (
          <p className={styles.rowSub}>
            <Link href={`/t/${tenantId}/${item.where}`}>Configurar</Link>
          </p>
        ) : null}
        {!item.enabled && blocked.length > 0 ? (
          <p className={styles.note}>
            Ligue antes: {blocked.map((key) => labels[key] ?? key).join(", ")}.
          </p>
        ) : null}
        {item.enabled && blocked.length > 0 ? (
          <p className={styles.note}>
            Ligado, mas parado: falta {blocked.map((key) => labels[key] ?? key).join(", ")}.
          </p>
        ) : null}
        {item.enabled && held.length > 0 ? (
          <p className={styles.note}>
            Em uso por {held.map((key) => labels[key] ?? key).join(", ")} — desligue esses antes.
          </p>
        ) : null}
      </div>
      <div className={styles.rowActions}>
        <Pill state={item.enabled ? "live" : "off"}>{item.enabled ? "Ligado" : "Desligado"}</Pill>
        {readOnly ? null : (
        <form action={toggleModule}>
          <input type="hidden" name="tenant_id" value={tenantId} />
          <input type="hidden" name="key" value={item.key} />
          <input type="hidden" name="enable" value={item.enabled ? "0" : "1"} />
          <button
            type="submit"
            className={`${styles.buttonGhost} ${styles.buttonSmall}`}
            disabled={item.enabled ? held.length > 0 : blocked.length > 0}
          >
            {item.enabled ? "Desligar" : "Ligar"}
          </button>
        </form>
        )}
      </div>
    </div>
  );
}

export default async function Modules({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!tenantScopes(me, context.tenant_id).can("settings:write")) notFound();
  // Ligar módulo é decisão de quem paga: o servidor só aceita da equipe da loja. O staff da
  // MuhBianco abre a tela para enxergar, sem botão que daria 403.
  const readOnly = !isStoreMember(me, context.tenant_id);

  const modules = await api<Module[]>(`/admin/tenants/${tenantId}/modules`);
  const free = modules.filter((m) => m.self_service);
  const paid = modules.filter((m) => !m.self_service);
  const accessMode = String(context.settings.storefront?.access_mode ?? "whitelist");

  // Rótulo por chave (para as frases de dependência) e um marcador do que está ligado.
  const labels: Record<string, string> = {};
  for (const item of modules) {
    labels[item.key] = item.label;
    if (item.enabled) labels[`on:${item.key}`] = "1";
  }

  // Ligado mas com dependência desligada: é o estado em que a vitrine abre e o carrinho trava.
  const broken = modules.filter(
    (item) => item.enabled && item.requires.some((key) => !labels[`on:${key}`]),
  );

  return (
    <>
      <PageHeader
        eyebrow="Loja"
        title="Módulos"
        lead="Ligue só o que a sua loja usa. O que está ligado aparece no menu e na vitrine; o que está desligado some sem apagar nada."
      />
      <Flash ok={ok} erro={erro} />

      {readOnly ? (
        <p className={styles.note}>
          Você está vendo esta loja como equipe MuhBianco. Quem liga e desliga módulo é a equipe
          da loja — aqui dá para conferir o estado, não para mudar.
        </p>
      ) : null}

      {broken.length ? (
        <Section
          title="Sua loja não consegue vender assim"
          description="Um módulo ligado depende de outro que está desligado."
        >
          <ul className={styles.steps}>
            {broken.map((item) => (
              <li key={item.key}>
                <strong>{item.label}</strong> está ligado, mas precisa de{" "}
                {item.requires
                  .filter((key) => !labels[`on:${key}`])
                  .map((key) => labels[key] ?? key)
                  .join(", ")}
                .
              </li>
            ))}
          </ul>
          <p className={styles.hint}>
            Enquanto isso, quem entra na loja vê os produtos e trava no carrinho.
          </p>
        </Section>
      ) : null}

      <Section
        title="O que a sua loja faz"
        description="Incluídos na mensalidade — ligue e desligue quando quiser."
      >
        <div className={styles.rows}>
          {free.map((item) => (
            <ModuleRow
              key={item.key}
              module={item}
              tenantId={tenantId}
              labels={labels}
              readOnly={readOnly}
            />
          ))}
        </div>
      </Section>

      <Section
        title="Quem vê a sua vitrine"
        description="Vale para a loja inteira; produtos continuam com as regras deles."
      >
        <form action={setAccessMode}>
          <input type="hidden" name="tenant_id" value={tenantId} />
          <div className={styles.rows}>
            {ACCESS.map((option) => (
              <label key={option.value} className={styles.check}>
                <input
                  type="radio"
                  name="access_mode"
                  value={option.value}
                  defaultChecked={accessMode === option.value}
                  disabled={readOnly}
                />
                <span>
                  {option.label}
                  <span className={styles.fieldHint}>{option.hint}</span>
                </span>
              </label>
            ))}
          </div>
          {readOnly ? null : (
            <div className={styles.formActions}>
              <button type="submit" className={styles.button}>
                Salvar
              </button>
            </div>
          )}
        </form>
      </Section>

      <Section
        title="Serviços à parte"
        description="Estes são contratados na sua conta MuhBianco, porque têm mensalidade própria."
      >
        <div className={styles.rows}>
          {paid.map((item) => (
            <div key={item.key} className={styles.row}>
              <div className={styles.rowMain}>
                <strong>{item.label}</strong>
                <p className={styles.rowSub}>{item.summary}</p>
                {item.locked_reason ? <p className={styles.note}>{item.locked_reason}</p> : null}
              </div>
              <div className={styles.rowActions}>
                <Pill state={item.enabled ? "live" : "off"}>
                  {item.enabled ? "Ligado" : "Desligado"}
                </Pill>
              </div>
            </div>
          ))}
        </div>
        <p className={styles.hint}>
          Para contratar ou cancelar, use{" "}
          <a href="https://muhbianco.com.br/conta.html" target="_blank" rel="noreferrer">
            sua conta MuhBianco
          </a>
          . Cancelando por lá, o serviço fica no ar até o fim do período já pago.
        </p>
      </Section>
    </>
  );
}
