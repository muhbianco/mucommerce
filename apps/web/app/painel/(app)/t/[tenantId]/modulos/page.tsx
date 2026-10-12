import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { isStoreMember, tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { PageHeader, Pill, Section } from "../ui";
import {
  removeStorePassword,
  setAccessMode,
  setStorePassword,
  toggleModule,
} from "./actions";

export const metadata: Metadata = { title: "Módulos da loja" };

interface Module {
  key: string;
  label: string;
  summary: string;
  where: string | null;
  /** Quem manda: a loja (aqui), a assinatura (conta MuhBianco) ou a MuhBianco (admin). */
  owner: "store" | "subscription" | "platform";
  self_service: boolean;
  /** Ligado sempre: aparece sem botão. */
  always_on: boolean;
  locked_reason: string | null;
  requires: string[];
  enabled: boolean;
  dependents: string[];
}

const ACCESS: { value: string; label: string; hint: string }[] = [
  {
    value: "public",
    label: "Aberta",
    hint: "Qualquer pessoa vê a loja inteira. O login só é pedido no carrinho.",
  },
  {
    value: "home_open",
    label: "Fechada, com a página inicial aberta",
    hint: "Todo mundo vê a página inicial, com os produtos que você marcar para aparecer nela. O resto da vitrine é para quem tem a senha da loja ou foi aprovado por você.",
  },
  {
    value: "closed",
    label: "Toda fechada",
    hint: "Nada aparece antes de entrar: com a senha da loja ou com uma conta que você aprovou.",
  },
];

interface StorePassword {
  set: boolean;
  masked: string | null;
  changed_at: string | null;
  password: string | null;
}

/** O modo salvo, nos termos das três opções da tela. */
function accessChoice(storefront: Record<string, unknown> | undefined): string {
  const mode = String(storefront?.access_mode ?? "whitelist");
  if (mode === "public" || mode === "login_required") return mode;
  return storefront?.landing_locked ? "closed" : "home_open";
}

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
            Ligado, mas parado: falta{" "}
            {blocked.map((key) => labels[key] ?? key).join(", ")}.
          </p>
        ) : null}
        {item.enabled && held.length > 0 ? (
          <p className={styles.note}>
            Em uso por {held.map((key) => labels[key] ?? key).join(", ")} —
            desligue esses antes.
          </p>
        ) : null}
      </div>
      <div className={styles.rowActions}>
        <Pill state={item.enabled ? "live" : "off"}>
          {item.always_on
            ? "Sempre ligado"
            : item.enabled
              ? "Ligado"
              : "Desligado"}
        </Pill>
        {readOnly || item.always_on ? null : (
          <form action={toggleModule}>
            <input type="hidden" name="tenant_id" value={tenantId} />
            <input type="hidden" name="key" value={item.key} />
            <input
              type="hidden"
              name="enable"
              value={item.enabled ? "0" : "1"}
            />
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
  searchParams: Promise<{ ok?: string; erro?: string; ver?: string }>;
}) {
  const { tenantId } = await params;
  const { ok, erro, ver } = await searchParams;
  const [context, me] = await Promise.all([
    loadTenantContext(tenantId),
    requireMe(),
  ]);
  if (!tenantScopes(me, context.tenant_id).can("settings:write")) notFound();
  // Ligar módulo é decisão de quem paga: o servidor só aceita da equipe da loja. O staff da
  // MuhBianco abre a tela para enxergar, sem botão que daria 403.
  const readOnly = !isStoreMember(me, context.tenant_id);

  const modules = await api<Module[]>(`/admin/tenants/${tenantId}/modules`);
  const free = modules.filter((m) => m.owner === "store");
  const paid = modules.filter((m) => m.owner === "subscription");
  const platform = modules.filter((m) => m.owner === "platform");
  const storefront = context.settings.storefront as
    Record<string, unknown> | undefined;
  const choice = accessChoice(storefront);
  const closed = choice !== "public";
  // A senha só existe para a equipe da loja: o staff da MuhBianco não a vê nem pela API.
  const password =
    closed && !readOnly
      ? await api<StorePassword>(
          `/admin/tenants/${tenantId}/storefront/password${ver === "1" ? "?ver=1" : ""}`,
        )
      : null;
  // `login_required` não é mais oferecido; só aparece para a loja que ainda está nele.
  const options =
    choice === "login_required"
      ? [
          ...ACCESS,
          {
            value: "login_required",
            label: "Só quem entrar (modo antigo)",
            hint: "Qualquer conta vê a vitrine, sem aprovação. Escolha uma das opções acima.",
          },
        ]
      : ACCESS;

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
          Você está vendo esta loja como equipe MuhBianco. Quem liga e desliga
          módulo é a equipe da loja — aqui dá para conferir o estado, não para
          mudar.
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
            Enquanto isso, quem entra na loja vê os produtos e trava no
            carrinho.
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
        id="vitrine"
        title="Quem vê a sua vitrine"
        description="Vale para a loja inteira. Na loja fechada com a página inicial aberta, cada produto diz se aparece nela (na tela do produto)."
      >
        <form action={setAccessMode}>
          <input type="hidden" name="tenant_id" value={tenantId} />
          <div className={styles.rows}>
            {options.map((option) => (
              <label key={option.value} className={styles.check}>
                <input
                  type="radio"
                  name="access_choice"
                  value={option.value}
                  defaultChecked={choice === option.value}
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
        {password ? (
          <div className={styles.rows}>
            <h3>Senha da loja (opcional)</h3>
            <p className={styles.hint}>
              Com senha, a porta da loja oferece duas entradas: a senha, que
              você passa a quem quiser, ou a conta Google, que você aprova na
              tela de Clientes. Quem entra pela senha só faz login no carrinho.
              Trocar a senha tranca de novo todo mundo que entrou com a
              anterior.
            </p>
            {password.set ? (
              <p>
                Senha atual:{" "}
                <strong>{password.password ?? password.masked}</strong>{" "}
                {password.password ? null : (
                  <Link href={`/t/${tenantId}/modulos?ver=1#vitrine`}>ver</Link>
                )}
              </p>
            ) : (
              <p className={styles.hint}>A loja ainda não tem senha.</p>
            )}
            <form action={setStorePassword} className={styles.fields}>
              <input type="hidden" name="tenant_id" value={tenantId} />
              <label className={styles.field}>
                {password.set ? "Nova senha" : "Senha"}
                <input
                  name="store_password"
                  minLength={4}
                  maxLength={64}
                  required
                  autoComplete="off"
                />
                <span className={styles.fieldHint}>
                  De 4 a 64 caracteres. Fácil de ditar por telefone.
                </span>
              </label>
              <div className={styles.formActions}>
                <button type="submit" className={styles.button}>
                  {password.set ? "Trocar senha" : "Definir senha"}
                </button>
              </div>
            </form>
            {password.set ? (
              <form action={removeStorePassword}>
                <input type="hidden" name="tenant_id" value={tenantId} />
                <button type="submit" className={styles.buttonGhost}>
                  Tirar a senha
                </button>
              </form>
            ) : null}
          </div>
        ) : null}
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
                {item.locked_reason ? (
                  <p className={styles.note}>{item.locked_reason}</p>
                ) : null}
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
          <a
            href="https://muhbianco.com.br/conta.html"
            target="_blank"
            rel="noreferrer"
          >
            sua conta MuhBianco
          </a>
          . Cancelando por lá, o serviço fica no ar até o fim do período já
          pago.
        </p>
      </Section>

      {platform.length ? (
        <Section
          title="Liberados pela MuhBianco"
          description="Não têm mensalidade; a MuhBianco liga quando a sua loja precisar."
        >
          <div className={styles.rows}>
            {platform.map((item) => (
              <div key={item.key} className={styles.row}>
                <div className={styles.rowMain}>
                  <strong>{item.label}</strong>
                  <p className={styles.rowSub}>{item.summary}</p>
                  {item.locked_reason ? (
                    <p className={styles.note}>{item.locked_reason}</p>
                  ) : null}
                </div>
                <div className={styles.rowActions}>
                  <Pill state={item.enabled ? "live" : "off"}>
                    {item.enabled ? "Ligado" : "Desligado"}
                  </Pill>
                </div>
              </div>
            ))}
          </div>
        </Section>
      ) : null}
    </>
  );
}
