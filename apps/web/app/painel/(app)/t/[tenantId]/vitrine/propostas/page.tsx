import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import { themeVariables, type Branding } from "@/lib/theme";

import { Block as StoreBlock } from "../../../../../../(storefront)/_store/landing-blocks";
import storeStyles from "../../../../../../(storefront)/_store/store.module.css";
import styles from "../../../../../panel.module.css";
import { Flash } from "../../flash";
import { EmptyState, PageHeader, Pill, Section } from "../../ui";
import vitrine from "../vitrine.module.css";
import { applyDraft, discardDraft, refineDraft, requestDraft } from "./actions";
import { DraftRefresher } from "./refresher";
import local from "./propostas.module.css";

export const metadata: Metadata = { title: "Propostas de página inicial" };

interface Draft {
  id: string;
  status: string;
  source: string;
  parent_draft_id: string | null;
  instruction: string | null;
  blocks: Record<string, unknown>[] | null;
  repaired: boolean;
  failure_reason: string | null;
  created_at: string;
  applied_at: string | null;
}

interface DraftList {
  drafts: Draft[];
  quota: { period: string; used: number; limit: number; left: number; paid: boolean };
  enabled: boolean;
}

type Resolved = Record<string, unknown> & { type: string };

/** Como cada situação aparece para a lojista, em palavras dela. */
const STATUS: Record<string, { label: string; state: "live" | "pending" | "off"; hint: string }> = {
  queued: { label: "Na fila", state: "pending", hint: "A montagem começa em instantes." },
  running: { label: "Montando", state: "pending", hint: "Costuma levar menos de um minuto." },
  ready: { label: "Pronta", state: "live", hint: "Dê uma olhada e decida." },
  failed: { label: "Não deu certo", state: "off", hint: "Não foi cobrada." },
  applied: { label: "Publicada", state: "live", hint: "Está no ar agora." },
  discarded: { label: "Descartada", state: "off", hint: "" },
};

const EM_ANDAMENTO = new Set(["queued", "running"]);

export default async function PropostasPage({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string; ver?: string }>;
}) {
  const { tenantId } = await params;
  const me = await requireMe();
  const context = await loadTenantContext(tenantId);
  if (!context) notFound();
  if (!tenantScopes(me, tenantId).can("settings:write")) notFound();

  const path = `/admin/tenants/${tenantId}`;
  const base = `/t/${tenantId}`;
  const { ok, erro, ver } = await searchParams;

  const lista = await api<DraftList>(`${path}/landing/drafts`);
  const montando = lista.drafts.some((d) => EM_ANDAMENTO.has(d.status));

  // A proposta aberta: a que ela pediu para ver, ou a primeira pronta.
  const aberta =
    lista.drafts.find((d) => d.id === ver) ?? lista.drafts.find((d) => d.status === "ready");
  const preview =
    aberta && aberta.blocks
      ? await api<Resolved[]>(`${path}/landing/drafts/${aberta.id}/preview`).catch(
          () => [] as Resolved[],
        )
      : [];
  const branding = (context.settings.branding ?? {}) as Branding;

  return (
    <>
      <PageHeader
        eyebrow="Vitrine"
        title="Propostas de página inicial"
        lead="A gente monta a partir do que você contou. Nada vai para o ar sem o seu OK."
        actions={
          <Pill state={lista.quota.left > 0 ? "live" : "off"}>
            {lista.quota.left > 0
              ? `${lista.quota.left} de ${lista.quota.limit} neste mês`
              : "acabaram as deste mês"}
          </Pill>
        }
      />
      <Flash ok={ok} erro={erro} />
      <DraftRefresher active={montando} />

      <Section
        title="Pedir uma proposta"
        description={`Você já usou ${lista.quota.used} de ${lista.quota.limit} neste mês`}
        actions={
          lista.enabled && lista.quota.left > 0 && !montando ? (
            <form action={requestDraft}>
              <input type="hidden" name="tenant_id" value={tenantId} />
              <button type="submit" className={styles.button}>
                Montar uma proposta
              </button>
            </form>
          ) : null
        }
      >
        {!lista.enabled ? (
          <p className={styles.hint}>
            A montagem com IA ainda não está disponível na sua loja. Assim que entrar no ar, o
            botão aparece aqui.
          </p>
        ) : montando ? (
          <p className={styles.hint}>
            Estamos montando a sua proposta. Isso costuma levar menos de um minuto — esta página se
            atualiza sozinha, ou{" "}
            <a href={`${base}/vitrine/propostas`}>clique aqui para atualizar</a>.
          </p>
        ) : lista.quota.left > 0 ? (
          <p className={styles.hint}>
            Antes de pedir, confira o que você já contou em{" "}
            <a href={`${base}/vitrine/brief/negocio`}>Sobre a sua loja</a>. Quanto mais completo,
            melhor a proposta.
          </p>
        ) : (
          <p className={styles.hint}>
            {lista.quota.paid
              ? "Você usou as propostas deste mês. No mês que vem o contador zera."
              : "Você usou as propostas incluídas deste mês. Para ter mais, contrate a montagem com IA na sua conta MuhBianco."}
          </p>
        )}
      </Section>

      {aberta && aberta.blocks ? (
        <Section
          title="Prévia"
          description="O mesmo desenho que a sua loja vai ter, se você publicar"
          actions={
            <>
              <form action={applyDraft}>
                <input type="hidden" name="tenant_id" value={tenantId} />
                <input type="hidden" name="draft_id" value={aberta.id} />
                <button type="submit" className={styles.button} disabled={aberta.status !== "ready"}>
                  Publicar esta página
                </button>
              </form>
              <form action={discardDraft}>
                <input type="hidden" name="tenant_id" value={tenantId} />
                <input type="hidden" name="draft_id" value={aberta.id} />
                <button type="submit" className={styles.buttonSmall}>
                  Descartar
                </button>
              </form>
            </>
          }
        >
          {aberta.repaired ? (
            <p className={styles.hint}>
              Ajustamos alguns detalhes da proposta automaticamente (uma imagem ou um produto que
              não existia mais). O que você vê abaixo é o resultado final.
            </p>
          ) : null}
          <div className={vitrine.canvas}>
            <div
              className={`${storeStyles.storefront} ${vitrine.canvasInner}`}
              style={themeVariables(branding)}
            >
              {preview.map((block, i) => (
                <StoreBlock
                  key={typeof block.id === "string" ? block.id : i}
                  block={block}
                  first={i === 0}
                  context={{ catalogOn: Boolean(context.features.catalog), chatUrl: null }}
                />
              ))}
            </div>
          </div>

          {lista.enabled && lista.quota.left > 0 ? (
            <form action={refineDraft} className={local.refine}>
              <input type="hidden" name="tenant_id" value={tenantId} />
              <input type="hidden" name="draft_id" value={aberta.id} />
              <label className={local.refineField} htmlFor="instrucao">
                Quer mudar alguma coisa? Diga em uma frase
              </label>
              <div className={local.refineRow}>
                <input
                  id="instrucao"
                  name="instruction"
                  maxLength={200}
                  placeholder="Ex.: mais curto, sem exclamação, e o horário logo no começo"
                />
                <button type="submit" className={styles.buttonSmall}>
                  Pedir de novo
                </button>
              </div>
              <small className={styles.hint}>
                Isso gasta mais uma das suas propostas do mês. Para trocar uma foto, reordenar ou
                mudar o arranjo de um bloco, use o{" "}
                <a href={`${base}/vitrine`}>editor da página inicial</a> — lá é de graça.
              </small>
            </form>
          ) : null}
        </Section>
      ) : null}

      <Section title="Histórico" description={`${lista.drafts.length} no total`}>
        {lista.drafts.length === 0 ? (
          <EmptyState title="Nenhuma proposta ainda.">
            Conte sobre a sua loja e peça a primeira: dá para trocar tudo depois, e nada vai para o
            ar sem você mandar.
          </EmptyState>
        ) : (
          <ul className={local.list}>
            {lista.drafts.map((draft) => {
              const estado = STATUS[draft.status] ?? {
                label: draft.status,
                state: "off" as const,
                hint: "",
              };
              return (
                <li key={draft.id} className={local.item} data-current={draft.id === aberta?.id ? "" : undefined}>
                  <div className={local.itemHead}>
                    <Pill state={estado.state}>{estado.label}</Pill>
                    <span className={local.itemWhen}>
                      {new Date(draft.created_at).toLocaleString("pt-BR")}
                    </span>
                    {draft.source === "refine" ? (
                      <span className={local.itemNote}>
                        pedido de mudança: “{draft.instruction}”
                      </span>
                    ) : null}
                  </div>
                  <p className={local.itemHint}>{draft.failure_reason || estado.hint}</p>
                  {draft.blocks ? (
                    <a className={styles.buttonSmall} href={`?ver=${draft.id}`}>
                      Ver esta
                    </a>
                  ) : null}
                </li>
              );
            })}
          </ul>
        )}
      </Section>
    </>
  );
}
