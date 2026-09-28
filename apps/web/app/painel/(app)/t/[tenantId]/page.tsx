import type { Metadata } from "next";
import Link from "next/link";

import { requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import type { PillState } from "@/lib/panel/states";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../panel.module.css";
import local from "./overview.module.css";
import { EmptyState, KeyValues, PageHeader, Pill, Section, Stat, Stats } from "./ui";

export const metadata: Metadata = { title: "Visão geral" };

interface StatusView {
  label: string;
  state: PillState;
  hint: string;
}

/** Situação da loja em palavras de lojista (o aviso de cobrança, quando há, aparece acima). */
const STORE_STATUS: Record<string, StatusView> = {
  active: { label: "No ar", state: "live", hint: "A sua vitrine está funcionando." },
  suspended: { label: "Em atraso", state: "warn", hint: "Assinatura em atraso: veja o aviso acima." },
  provisioning: { label: "Preparando", state: "pending", hint: "A MuhBianco está montando a sua loja." },
  draft: { label: "Em montagem", state: "pending", hint: "A vitrine ainda não abriu ao público." },
  archived: { label: "Arquivada", state: "off", hint: "A vitrine está fora do ar." },
};

/** Quem vê o catálogo, conforme o modo de acesso da vitrine. */
const ACCESS: Record<string, { label: string; hint: string }> = {
  public: { label: "Aberta", hint: "Qualquer pessoa vê o catálogo." },
  login_required: { label: "Com login", hint: "Só quem entra com a conta vê o catálogo." },
  whitelist: { label: "Só aprovados", hint: "Só quem você liberar vê o catálogo." },
};

const MODULES: { key: string; label: string }[] = [
  { key: "storefront", label: "Vitrine" },
  { key: "catalog", label: "Catálogo" },
  { key: "inventory", label: "Estoque" },
  { key: "events", label: "Eventos" },
  { key: "checkout", label: "Carrinho e pagamento" },
  { key: "pickup", label: "Retirada" },
  { key: "delivery", label: "Entrega" },
  { key: "coupons", label: "Cupons" },
  { key: "shipping.melhorenvio", label: "Envio por transportadora" },
];

interface Shortcut {
  /** Nome da aba no menu, para a pessoa aprender onde fica. */
  tab: string;
  href: string;
  /** A tarefa, em verbo; é o nome do link (não repete o nome da aba). */
  title: string;
  text: string;
}

function Shortcuts({ items }: { items: Shortcut[] }) {
  return (
    <div className={local.shortcuts}>
      {items.map((item) => (
        <div key={item.href} className={local.shortcut}>
          <span className={local.shortcutTab}>{item.tab}</span>
          <Link href={item.href} className={local.shortcutLink}>
            {item.title}
            <span aria-hidden="true"> →</span>
          </Link>
          <p className={local.shortcutText}>{item.text}</p>
        </div>
      ))}
    </div>
  );
}

function present(items: (Shortcut | false | undefined)[]): Shortcut[] {
  return items.filter((item): item is Shortcut => Boolean(item));
}

export default async function TenantOverview({ params }: { params: Promise<{ tenantId: string }> }) {
  const { tenantId } = await params;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  const scopes = tenantScopes(me, context.tenant_id);
  const base = `/t/${encodeURIComponent(context.tenant_id)}`;
  const f = context.features;
  const accessMode = String(context.settings.storefront?.access_mode ?? "whitelist");

  const status: StatusView = STORE_STATUS[context.status] ?? { label: context.status, state: "off", hint: "" };
  const access = ACCESS[accessMode] ?? { label: accessMode, hint: "" };
  const modulesOn = MODULES.filter((module) => f[module.key]).length;
  // Quem pode mexer vê os cartões como atalho; quem não pode, como informação.
  const settings = scopes.can("settings:write");

  // Mesmas condições das abas do menu (layout.tsx): atalho só para o que a pessoa pode abrir.
  const catalog = f.catalog && scopes.can("catalog:read");
  const daily = present([
    scopes.can("orders:read") &&
      f.checkout && {
        tab: "Pedidos",
        href: `${base}/pedidos`,
        title: "Acompanhar as vendas",
        text: "Aceite, prepare e marque o que já foi entregue.",
      },
    catalog && {
      tab: "Produtos",
      href: `${base}/produtos`,
      title: "Cadastrar um produto",
      text: "Foto, preço e descrição; publique quando estiver pronto.",
    },
    catalog &&
      f.inventory &&
      scopes.can("inventory:read") && {
        tab: "Estoque",
        href: `${base}/estoque`,
        title: "Conferir o estoque",
        text: "Veja o que está acabando e registre entradas e perdas.",
      },
    scopes.can("customers:read") && {
      tab: "Clientes",
      href: `${base}/clientes`,
      ...(accessMode === "whitelist"
        ? { title: "Liberar acesso à loja", text: "Aprove quem pediu para ver o catálogo." }
        : { title: "Ver quem se cadastrou", text: "Consulte os cadastros e bloqueie quem precisar." }),
    },
    scopes.can("settings:write") &&
      f.coupons && {
        tab: "Cupons",
        href: `${base}/cupons`,
        title: "Criar um cupom",
        text: "Desconto por código, com validade e limite de uso.",
      },
  ]);
  const setup = present([
    catalog && {
      tab: "Categorias",
      href: `${base}/categorias`,
      title: "Organizar as categorias",
      text: "Agrupe o que você vende para facilitar a busca na loja.",
    },
    scopes.can("settings:write") &&
      (f.checkout || f.pickup || f.delivery) && {
        tab: "Entrega e checkout",
        href: `${base}/entrega`,
        title: "Definir entrega e retirada",
        text: "Regiões e taxas, pontos de retirada, horários e regras do checkout.",
      },
    scopes.can("payments:read") &&
      f.checkout && {
        tab: "Pagamentos",
        href: `${base}/pagamentos`,
        title: "Configurar os recebimentos",
        text: "Conecte a conta onde você recebe por Pix e cartão.",
      },
    scopes.can("domains:write") && {
      tab: "Endereços",
      href: `${base}/dominios`,
      title: "Conectar um domínio seu",
      text: "Use o endereço da sua marca; o da MuhBianco continua valendo.",
    },
    scopes.can("settings:write") && {
      tab: "Configurações",
      href: `${base}/configuracoes`,
      title: "Personalizar a loja",
      text: "Logo, cores, página inicial, SEO e termos de uso.",
    },
  ]);

  return (
    <>
      <PageHeader
        eyebrow="Visão geral"
        title="Resumo da sua loja"
        lead="Veja se a vitrine está no ar e quem pode ver o catálogo, e vá direto para o que precisa fazer."
      />

      <Stats>
        <Stat
          label="Situação"
          value={
            <span className={local.status} data-state={status.state}>
              {status.label}
            </span>
          }
          hint={status.hint || undefined}
        />
        <Stat
          label="Endereço principal"
          value={
            context.primary_host ? (
              <span className={local.valueText}>
                <a href={`https://${context.primary_host}`} target="_blank" rel="noreferrer">
                  {context.primary_host} ↗
                </a>
              </span>
            ) : (
              "Sem endereço"
            )
          }
          hint={
            scopes.can("domains:write") ? (
              <Link href={`${base}/dominios`}>Ver todos os endereços</Link>
            ) : context.primary_host ? (
              "É o que aparece nos links da loja."
            ) : undefined
          }
        />
        <Stat
          label="Acesso à vitrine"
          value={access.label}
          hint={settings ? "Trocar quem pode ver" : access.hint || undefined}
          href={settings ? `${base}/modulos` : undefined}
        />
        <Stat
          label="Módulos"
          value={`${modulesOn} de ${MODULES.length}`}
          hint={settings ? "Ligar e desligar" : "ligados nesta loja"}
          href={settings ? `${base}/modulos` : undefined}
        />
      </Stats>

      <div className={styles.split}>
        <div>
          {daily.length ? (
            <Section title="Dia a dia" description="O que você faz toda semana">
              <Shortcuts items={daily} />
            </Section>
          ) : null}
          {setup.length ? (
            <Section title="Montar a loja" description="Configure uma vez e ajuste quando precisar">
              <Shortcuts items={setup} />
            </Section>
          ) : null}
          {daily.length || setup.length ? null : (
            <Section title="Atalhos">
              <EmptyState title="Por aqui, só o resumo">
                O seu papel nesta loja não abre outras áreas do painel. Se precisar de mais, fale com o dono da
                loja.
              </EmptyState>
            </Section>
          )}
        </div>

        <aside>
          <Section
            title="Módulos"
            description="O que está ligado"
            id="modulos"
            actions={settings ? <Link href={`${base}/modulos`}>Ligar e desligar</Link> : undefined}
          >
            <KeyValues
              items={MODULES.map((module) => ({
                label: module.label,
                value: f[module.key] ? <Pill state="live">Ligado</Pill> : <Pill state="off">Desligado</Pill>,
              }))}
            />
          </Section>
          <Section title="Dados da loja">
            <KeyValues
              items={[
                { label: "Fuso horário", value: context.timezone },
                { label: "Moeda", value: context.currency },
              ]}
            />
          </Section>
        </aside>
      </div>
    </>
  );
}
