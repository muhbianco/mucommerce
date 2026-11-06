import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Media } from "@/lib/panel/types";
import type { Branding, StoreDesign } from "@/lib/theme";

import styles from "../../../../../panel.module.css";
import { Flash } from "../../flash";
import { ImageUploader } from "../../image-uploader";
import { EmptyState, PageHeader, Pill, Section } from "../../ui";
import {
  applyDraft,
  discardDraft,
  refineDraft,
  requestDraft,
  resetBrandReading,
  revertDraft,
  setMediaRole,
} from "./actions";
import { DraftRefresher } from "./refresher";
import local from "./propostas.module.css";
import { StorePreview, Swatch } from "./store-preview";

export const metadata: Metadata = { title: "Identidade da loja" };

interface Change {
  scope: string;
  label: string;
  before: string;
  after: string;
}

interface Summary {
  text?: string;
  decisions?: { area: string; why: string }[];
  changes?: Change[];
  fixes?: string[];
  profile?: {
    source?: string;
    refreshed?: boolean;
    changes?: string[];
    contradictions?: string[];
    pending?: number;
    note?: string | null;
  };
}

interface Draft {
  id: string;
  status: string;
  source: string;
  parent_draft_id: string | null;
  instruction: string | null;
  blocks: Record<string, unknown>[] | null;
  branding: Branding | null;
  design: StoreDesign | null;
  summary: Summary | null;
  repaired: boolean;
  failure_reason: string | null;
  /** Quando a proposta foi montada. */
  created_at: string;
  /** A última vez que entrou no ar. */
  applied_at: string | null;
  /** Quando saiu do ar. Nulo na que está no ar e na que nunca foi publicada. */
  replaced_at: string | null;
  /** É a que está no ar agora (no máximo uma). */
  live: boolean;
  /** Pode ser publicada (de novo). */
  publishable: boolean;
  edited_since: boolean;
  revertible: boolean;
}

interface DraftList {
  drafts: Draft[];
  quota: { period: string; used: number; limit: number; left: number; paid: boolean };
  enabled: boolean;
}

interface BrandMaterial {
  media_id: string;
  kind: string | null;
  relevance: string | null;
  description: string | null;
  usable_on_page: boolean;
  superseded_by: string | null;
  source: string | null;
}

interface BrandState {
  profile: {
    summary: string;
    personality: Record<string, string>;
    palette: { primary: string | null; secondary: string | null; accent: string | null; surface: string };
    typography: { headings: string; case: string };
    preserve: string[];
    avoid: string[];
    inferred: string[];
    confidence: string;
  } | null;
  version: number;
  source: string | null;
  updated_at: string | null;
  changes: string[];
  contradictions: string[];
  pending: number;
  materials: BrandMaterial[];
}

type Resolved = Record<string, unknown> & { type: string };

/** Como cada situação aparece para a lojista, em palavras dela. */
const STATUS: Record<string, { label: string; state: "live" | "pending" | "off"; hint: string }> = {
  queued: { label: "Na fila", state: "pending", hint: "A montagem começa em instantes." },
  running: { label: "Montando", state: "pending", hint: "Lendo o seu material e desenhando a loja. Leva até um minuto." },
  ready: { label: "Pronta, não publicada", state: "pending", hint: "Dê uma olhada e decida." },
  failed: { label: "Não deu certo", state: "off", hint: "Não foi cobrada." },
  applied: { label: "No ar", state: "live", hint: "É o que os clientes veem agora." },
  superseded: { label: "Já esteve no ar", state: "off", hint: "Dá para publicar de novo." },
  discarded: { label: "Descartada", state: "off", hint: "Nunca foi publicada. Dá para publicar se mudar de ideia." },
  reverted: { label: "Desfeita", state: "off", hint: "Saiu do ar por um desfazer. Dá para publicar de novo." },
};

const EM_ANDAMENTO = new Set(["queued", "running"]);

/** O papel da imagem, como a lojista o escolhe. */
const ROLE_OPTIONS: { value: string; label: string }[] = [
  { value: "logo", label: "Logotipo" },
  { value: "banner", label: "Foto grande / banner" },
  { value: "place_photo", label: "A loja por dentro ou por fora" },
  { value: "product_photo", label: "Foto de produto" },
  { value: "team_photo", label: "Eu ou a equipe" },
  { value: "texture", label: "Textura / padrão gráfico" },
  { value: "other", label: "Outra coisa" },
];

/** O que a leitura da marca disse que cada arquivo é. */
const KIND_LABEL: Record<string, string> = {
  logo: "logotipo",
  emblem: "emblema",
  banner: "banner",
  product_photo: "foto de produto",
  lifestyle: "foto de uso",
  place: "a loja",
  team: "equipe",
  promo: "promoção",
  texture: "textura",
  screenshot: "print de tela",
  other: "outra coisa",
};

const RELEVANCE_LABEL: Record<string, string> = {
  identity: "identidade",
  product: "produto",
  promotional_temporary: "promoção temporária",
  reference: "referência",
  irrelevant: "não usada",
};

const SURFACE_LABEL: Record<string, string> = { light: "fundo claro", warm: "fundo creme", dark: "fundo escuro" };

const HEADING_LABEL: Record<string, string> = {
  system: "letra simples",
  serif: "letra serifada",
  rounded: "letra arredondada",
  display: "letra condensada forte",
  elegant: "letra elegante",
  geometric: "letra geométrica",
};

const DECISION_LABEL: Record<string, string> = {
  marca: "Marca",
  cabecalho: "Cabeçalho",
  vitrine: "Vitrine",
  produto: "Produto",
  checkout: "Carrinho e checkout",
  rodape: "Rodapé",
  pagina_inicial: "Página inicial",
};

const HEX = /^#[0-9a-fA-F]{6}$/;

export default async function PropostasPage({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string; ver?: string; tela?: string; antes?: string }>;
}) {
  const { tenantId } = await params;
  const me = await requireMe();
  const context = await loadTenantContext(tenantId);
  if (!context) notFound();
  if (!tenantScopes(me, tenantId).can("settings:write")) notFound();

  const path = `/admin/tenants/${tenantId}`;
  const base = `/t/${tenantId}`;
  const { ok, erro, ver, tela, antes } = await searchParams;
  const phone = tela === "celular";

  const [lista, material, marca] = await Promise.all([
    api<DraftList>(`${path}/landing/drafts`),
    api<Media[]>(`${path}/media?owner_type=landing`),
    api<BrandState>(`${path}/landing/brand`).catch(() => null),
  ]);
  const montando = lista.drafts.some((d) => EM_ANDAMENTO.has(d.status));
  const leitura = new Map((marca?.materials ?? []).map((m) => [m.media_id, m]));

  // A proposta aberta: a que ela pediu para ver, ou a primeira pronta.
  const aberta = lista.drafts.find((d) => d.id === ver) ?? lista.drafts.find((d) => d.status === "ready");
  const comoHoje = Boolean(antes) && Boolean(aberta);

  const brandingAtual = (context.settings.branding ?? {}) as Branding;
  const designAtual = (context.settings.design ?? {}) as StoreDesign;
  const brandingPrevia: Branding = comoHoje
    ? brandingAtual
    : { ...brandingAtual, ...(aberta?.branding ?? {}) };
  const designPrevia: StoreDesign = comoHoje ? designAtual : (aberta?.design ?? designAtual);

  const preview =
    aberta && aberta.blocks
      ? await api<Resolved[]>(
          comoHoje ? `${path}/landing/preview` : `${path}/landing/drafts/${aberta.id}/preview`,
        ).catch(() => [] as Resolved[])
      : [];
  const logo = context.settings.branding?.logo_media_id
    ? await api<Media>(`${path}/media/${String(context.settings.branding.logo_media_id)}`).catch(() => null)
    : null;
  const logoUrl = logo?.renditions?.[0]?.url ?? null;

  const verLink = (extra: Record<string, string | undefined>) => {
    const q = new URLSearchParams();
    if (aberta) q.set("ver", aberta.id);
    for (const [k, v] of Object.entries(extra)) if (v) q.set(k, v);
    return `?${q.toString()}`;
  };

  const resumo = aberta?.summary ?? null;
  const proposta = aberta && (aberta.branding || aberta.design);

  // Datas no fuso da loja: renderiza no servidor (UTC), e sem timeZone 10:59 saía 13:59.
  const quando = (iso: string | null) =>
    iso
      ? new Date(iso).toLocaleString("pt-BR", {
          timeZone: context.timezone,
          day: "2-digit",
          month: "2-digit",
          year: "numeric",
          hour: "2-digit",
          minute: "2-digit",
        })
      : "";
  // Versão = proposta que ficou pronta alguma vez. Numeradas da mais antiga para a mais nova,
  // para "Versão 3" querer dizer a mesma coisa amanhã. Montagem que falhou não é versão.
  const versoes = lista.drafts.filter((d) => d.blocks && !EM_ANDAMENTO.has(d.status));
  const numero = new Map(
    [...versoes].sort((a, b) => a.created_at.localeCompare(b.created_at)).map((d, i) => [d.id, i + 1]),
  );
  const noAr = versoes.find((d) => d.live) ?? null;
  const outras = versoes.filter((d) => !d.live);
  const falhas = lista.drafts.filter((d) => d.status === "failed");
  const andamento = lista.drafts.filter((d) => EM_ANDAMENTO.has(d.status));
  const nomeDa = (d: Draft) => `Versão ${numero.get(d.id) ?? "?"}`;

  return (
    <>
      <PageHeader
        eyebrow="Vitrine"
        title="Identidade da loja"
        lead="A gente lê o seu material, entende como a sua marca se expressa e propõe a loja inteira: cores, letras, botões, cabeçalho, vitrine, página de produto, carrinho, checkout e página inicial. Nada vai para o ar sem o seu OK — e dá para voltar."
        actions={
          <Pill state={lista.quota.left > 0 ? "live" : "off"}>
            {lista.quota.left > 0 ? `restam ${lista.quota.left}` : "acabaram as deste mês"}
          </Pill>
        }
      />
      <Flash ok={ok} erro={erro} />
      <DraftRefresher active={montando} />

      <Section
        title="1. O material da sua loja"
        description={`${material.length} ${material.length === 1 ? "imagem" : "imagens"} · o logotipo fica em Marca; as fotos dos produtos entram sozinhas`}
      >
        <p className={styles.hint}>
          Mande logotipo, banners, fotos da loja, posts do Instagram — o que mostre como a sua marca é. A gente olha cada
          imagem e diz o que entendeu; se errar, corrija o que é cada uma.
        </p>
        {material.length ? (
          <div className={local.material}>
            {material.map((media) => {
              const lido = leitura.get(media.id);
              return (
                <figure key={media.id} className={local.materialTile}>
                  {media.renditions.length ? (
                    // eslint-disable-next-line @next/next/no-img-element
                    <img src={media.renditions[0]!.url} alt={media.alt ?? ""} />
                  ) : (
                    <div className={local.materialPending}>processando…</div>
                  )}
                  <figcaption>
                    {lido?.kind ? (
                      <span className={local.reading}>
                        A gente viu: <strong>{KIND_LABEL[lido.kind] ?? lido.kind}</strong>
                        {lido.relevance ? ` · ${RELEVANCE_LABEL[lido.relevance] ?? lido.relevance}` : ""}
                        {lido.superseded_by ? " · substituída por uma mais nova" : ""}
                      </span>
                    ) : (
                      <span className={local.reading}>Ainda não lida</span>
                    )}
                    <form action={setMediaRole} className={local.roleForm}>
                      <input type="hidden" name="tenant_id" value={tenantId} />
                      <input type="hidden" name="media_id" value={media.id} />
                      <label className={local.srOnly} htmlFor={`papel-${media.id}`}>
                        O que é esta imagem
                      </label>
                      <select id={`papel-${media.id}`} name="role" defaultValue={media.role ?? ""}>
                        <option value="" disabled>
                          O que é?
                        </option>
                        {ROLE_OPTIONS.map((r) => (
                          <option key={r.value} value={r.value}>
                            {r.label}
                          </option>
                        ))}
                      </select>
                      <button type="submit" className={styles.buttonSmall}>
                        Salvar
                      </button>
                    </form>
                  </figcaption>
                </figure>
              );
            })}
          </div>
        ) : null}
        <ImageUploader tenantId={tenantId} ownerType="landing" label="Enviar imagens da marca" askRole />
      </Section>

      <Section
        title="2. A identidade que a gente identificou"
        description={
          marca?.profile
            ? marca.source === "llm"
              ? "Lida nas suas imagens e no que você contou"
              : "Medida no logotipo e no que você contou — as imagens ainda não foram lidas"
            : "Aparece na primeira proposta"
        }
        actions={
          marca?.profile ? (
            <form action={resetBrandReading}>
              <input type="hidden" name="tenant_id" value={tenantId} />
              <button type="submit" className={styles.buttonSmall}>
                Ler todo o material de novo
              </button>
            </form>
          ) : null
        }
      >
        {marca?.profile ? (
          <div className={local.identity}>
            <p className={local.identitySummary}>{marca.profile.summary}</p>
            <div className={local.chips}>
              {Object.values(marca.profile.personality)
                .filter((v) => v !== "equilibrada" && v !== "intermediaria")
                .map((v) => (
                  <span key={v} className={local.chip}>
                    {v}
                  </span>
                ))}
              <span className={local.chip}>{SURFACE_LABEL[marca.profile.palette.surface] ?? marca.profile.palette.surface}</span>
              <span className={local.chip}>
                {HEADING_LABEL[marca.profile.typography.headings] ?? marca.profile.typography.headings}
                {marca.profile.typography.case === "upper" ? ", caixa alta" : ""}
              </span>
            </div>
            <div className={local.swatches}>
              {marca.profile.palette.primary && HEX.test(marca.profile.palette.primary) ? (
                <Swatch color={marca.profile.palette.primary} label="principal" />
              ) : null}
              {marca.profile.palette.secondary && HEX.test(marca.profile.palette.secondary) ? (
                <Swatch color={marca.profile.palette.secondary} label="destaque" />
              ) : null}
              {marca.profile.palette.accent && HEX.test(marca.profile.palette.accent) ? (
                <Swatch color={marca.profile.palette.accent} label="apoio" />
              ) : null}
            </div>
            {marca.profile.preserve.length ? (
              <p className={styles.hint}>A gente preserva: {marca.profile.preserve.join(" · ")}</p>
            ) : null}
            {marca.changes.length ? (
              <p className={styles.hint}>O que o material novo mudou: {marca.changes.join(" · ")}</p>
            ) : null}
            {marca.contradictions.length ? (
              <p className={styles.hint}>Encontramos material que se contradiz: {marca.contradictions.join(" · ")}</p>
            ) : null}
            {marca.pending ? (
              <p className={styles.hint}>
                {marca.pending} {marca.pending === 1 ? "imagem ainda não foi lida" : "imagens ainda não foram lidas"}: entram
                na próxima proposta.
              </p>
            ) : null}
            {marca.profile.confidence === "baixa" && marca.profile.inferred.length ? (
              <p className={styles.hint}>Deduzido, não visto: {marca.profile.inferred.join(" · ")}</p>
            ) : null}
          </div>
        ) : (
          <p className={styles.hint}>
            Conte sobre a sua loja em <a href={`${base}/vitrine/brief/negocio`}>Sobre a sua loja</a>, mande o material e peça
            uma proposta: a primeira leitura da sua marca aparece aqui.
          </p>
        )}
      </Section>

      <Section
        title="3. Pedir uma proposta"
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
            A montagem com IA ainda não está disponível na sua loja. Assim que entrar no ar, o botão aparece aqui.
          </p>
        ) : montando ? (
          <p className={styles.hint} role="status">
            Estamos lendo o seu material e desenhando a loja. Leva até um minuto — esta página se atualiza sozinha, ou{" "}
            <a href={`${base}/vitrine/propostas`}>clique aqui para atualizar</a>.
          </p>
        ) : lista.quota.left > 0 ? (
          <p className={styles.hint}>
            Cada proposta lê só o material novo e redesenha o que fizer sentido. Antes de pedir, confira{" "}
            <a href={`${base}/vitrine/brief/negocio`}>Sobre a sua loja</a>.
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
          title={`4. ${aberta.status === "ready" ? "A proposta" : nomeDa(aberta)}`}
          description={
            aberta.live
              ? `No ar desde ${quando(aberta.applied_at)}`
              : proposta
                ? "A loja inteira, como fica se você publicar"
                : "Versão antiga: só a página inicial"
          }
          actions={
            <>
              {aberta.publishable ? (
                <form action={applyDraft}>
                  <input type="hidden" name="tenant_id" value={tenantId} />
                  <input type="hidden" name="draft_id" value={aberta.id} />
                  <button type="submit" className={styles.button}>
                    {aberta.status === "ready" ? "Publicar na loja" : "Publicar esta versão"}
                  </button>
                </form>
              ) : null}
              {aberta.status === "ready" ? (
                <form action={discardDraft}>
                  <input type="hidden" name="tenant_id" value={tenantId} />
                  <input type="hidden" name="draft_id" value={aberta.id} />
                  <button type="submit" className={styles.buttonSmall}>
                    Descartar
                  </button>
                </form>
              ) : null}
              {aberta.revertible ? (
                <form action={revertDraft}>
                  <input type="hidden" name="tenant_id" value={tenantId} />
                  <input type="hidden" name="draft_id" value={aberta.id} />
                  <button type="submit" className={styles.buttonSmall}>
                    Desfazer esta publicação
                  </button>
                </form>
              ) : null}
            </>
          }
        >
          {aberta.edited_since && aberta.status === "ready" ? (
            <p className={local.warning} role="alert">
              Você mudou a loja depois desta proposta. Publicar substitui o que você fez — e dá para voltar logo depois, em
              Versões.
            </p>
          ) : null}
          {aberta.edited_since && aberta.live ? (
            <p className={local.warning}>
              Você mudou a loja à mão depois que esta versão entrou no ar: o que está no ar é ela com as suas mudanças.
            </p>
          ) : null}

          {resumo?.text ? <p className={local.identitySummary}>{resumo.text}</p> : null}

          {resumo?.changes?.length ? (
            <div className={local.changes}>
              <h3 className={local.subhead}>
                {aberta.status === "ready" ? "O que muda" : "O que esta versão mudou em relação ao que estava no ar quando foi montada"}
              </h3>
              <ul>
                {resumo.changes.map((c) => (
                  <li key={`${c.scope}-${c.label}`}>
                    <strong>{c.label}:</strong>{" "}
                    {HEX.test(c.before) ? <Swatch color={c.before} label="" /> : <span className="muted">{c.before}</span>} →{" "}
                    {HEX.test(c.after) ? <Swatch color={c.after} label="" /> : <span>{c.after}</span>}
                  </li>
                ))}
              </ul>
            </div>
          ) : proposta ? (
            <p className={styles.hint}>Esta proposta não muda nada em relação ao que está no ar.</p>
          ) : null}

          {resumo?.decisions?.length ? (
            <div className={local.changes}>
              <h3 className={local.subhead}>Por quê</h3>
              <ul>
                {resumo.decisions.map((d, i) => (
                  <li key={i}>
                    <strong>{DECISION_LABEL[d.area] ?? d.area}:</strong> {d.why}
                  </li>
                ))}
              </ul>
            </div>
          ) : null}

          {resumo?.profile?.note ? <p className={styles.hint}>{resumo.profile.note}</p> : null}
          {resumo?.fixes?.length || aberta.repaired ? (
            <p className={styles.hint}>
              Ajustamos alguns detalhes sozinhos para a loja continuar legível e funcionando
              {resumo?.fixes?.length ? `: ${resumo.fixes.join("; ")}` : ""}.
            </p>
          ) : null}

          <div className={local.previewBar} role="group" aria-label="Como ver a prévia">
            <a className={styles.buttonSmall} href={verLink({ tela: phone ? undefined : "celular", antes })}>
              {phone ? "Ver no computador" : "Ver no celular"}
            </a>
            <a className={styles.buttonSmall} href={verLink({ tela, antes: comoHoje ? undefined : "1" })}>
              {comoHoje ? "Ver a proposta" : "Ver como está hoje"}
            </a>
            <span className={styles.hint}>{comoHoje ? "Mostrando a loja como está no ar." : "Mostrando a proposta."}</span>
          </div>

          <StorePreview
            branding={brandingPrevia}
            design={designPrevia}
            blocks={preview}
            storeName={context.name}
            logoUrl={logoUrl}
            catalogOn={Boolean(context.features.catalog)}
            phone={phone}
          />

          {lista.enabled && lista.quota.left > 0 && aberta.status === "ready" ? (
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
                  placeholder="Ex.: mais vibrante, botões maiores, checkout mais limpo"
                />
                <button type="submit" className={styles.buttonSmall}>
                  Pedir ajuste
                </button>
              </div>
              <small className={styles.hint}>
                Isso gasta mais uma das suas propostas do mês. Para trocar uma foto ou reordenar a página inicial, use o{" "}
                <a href={`${base}/vitrine`}>editor da página inicial</a> — lá é de graça.
              </small>
            </form>
          ) : null}
        </Section>
      ) : null}

      <Section
        title="5. Versões"
        description={
          versoes.length
            ? `${versoes.length} ${versoes.length === 1 ? "versão" : "versões"} · ${noAr ? `no ar: ${nomeDa(noAr)}` : "nenhuma no ar"}`
            : "Nenhuma ainda"
        }
      >
        {versoes.length === 0 && andamento.length === 0 && falhas.length === 0 ? (
          <EmptyState title="Nenhuma proposta ainda.">
            Mande o material da marca e peça a primeira: nada vai para o ar sem você mandar, e dá para voltar.
          </EmptyState>
        ) : (
          <>
            <h3 className={local.subhead}>No ar agora</h3>
            {noAr ? (
              <div className={local.versionLive} data-current={noAr.id === aberta?.id ? "" : undefined}>
                <div className={local.itemHead}>
                  <strong className={local.versionName}>{nomeDa(noAr)}</strong>
                  <Pill state="live">No ar</Pill>
                  {noAr.source === "refine" ? (
                    <span className={local.itemNote}>ajuste pedido: “{noAr.instruction}”</span>
                  ) : null}
                </div>
                <dl className={local.versionDates}>
                  <div>
                    <dt>Criada em</dt>
                    <dd>{quando(noAr.created_at)}</dd>
                  </div>
                  <div>
                    <dt>No ar desde</dt>
                    <dd>{quando(noAr.applied_at)}</dd>
                  </div>
                </dl>
                {noAr.edited_since ? (
                  <p className={local.itemHint}>Com mudanças que você fez à mão depois.</p>
                ) : null}
                <div className={local.versionActions}>
                  <a className={styles.buttonSmall} href={`?ver=${noAr.id}`}>
                    Ver
                  </a>
                  {noAr.revertible ? (
                    <form action={revertDraft}>
                      <input type="hidden" name="tenant_id" value={tenantId} />
                      <input type="hidden" name="draft_id" value={noAr.id} />
                      <button type="submit" className={styles.buttonSmall}>
                        Desfazer esta publicação
                      </button>
                    </form>
                  ) : null}
                </div>
              </div>
            ) : (
              <p className={styles.hint}>
                Nenhuma versão está no ar: a loja está com a página que você montou à mão (ou com a que voltou de um
                desfazer). Publique uma das versões abaixo quando quiser.
              </p>
            )}

            {andamento.map((d) => (
              <p key={d.id} className={styles.hint} role="status">
                {STATUS[d.status]?.label ?? d.status}: proposta pedida em {quando(d.created_at)}.
              </p>
            ))}

            {outras.length ? (
              <>
                <h3 className={local.subhead}>Outras versões</h3>
                <ul className={local.list}>
                  {outras.map((d) => {
                    const estado = STATUS[d.status] ?? { label: d.status, state: "off" as const, hint: "" };
                    return (
                      <li key={d.id} className={local.item} data-current={d.id === aberta?.id ? "" : undefined}>
                        <div className={local.itemHead}>
                          <strong className={local.versionName}>{nomeDa(d)}</strong>
                          <Pill state={estado.state}>{estado.label}</Pill>
                          {d.source === "refine" ? (
                            <span className={local.itemNote}>ajuste pedido: “{d.instruction}”</span>
                          ) : null}
                        </div>
                        <dl className={local.versionDates}>
                          <div>
                            <dt>Criada em</dt>
                            <dd>{quando(d.created_at)}</dd>
                          </div>
                          {d.applied_at ? (
                            <div>
                              <dt>{d.replaced_at ? "Esteve no ar" : "Publicada em"}</dt>
                              <dd>
                                {d.replaced_at
                                  ? `de ${quando(d.applied_at)} até ${quando(d.replaced_at)}`
                                  : quando(d.applied_at)}
                              </dd>
                            </div>
                          ) : (
                            <div>
                              <dt>Publicada</dt>
                              <dd>nunca</dd>
                            </div>
                          )}
                        </dl>
                        <div className={local.versionActions}>
                          <a className={styles.buttonSmall} href={`?ver=${d.id}`}>
                            Ver
                          </a>
                          {d.publishable ? (
                            <form action={applyDraft}>
                              <input type="hidden" name="tenant_id" value={tenantId} />
                              <input type="hidden" name="draft_id" value={d.id} />
                              <button type="submit" className={styles.buttonSmall}>
                                {d.status === "ready" ? "Publicar" : "Publicar esta versão"}
                              </button>
                            </form>
                          ) : null}
                        </div>
                      </li>
                    );
                  })}
                </ul>
              </>
            ) : null}

            {falhas.length ? (
              <details className={local.failures}>
                <summary>
                  {falhas.length} {falhas.length === 1 ? "tentativa que não deu certo" : "tentativas que não deram certo"}{" "}
                  (não foram cobradas)
                </summary>
                <ul>
                  {falhas.map((d) => (
                    <li key={d.id}>
                      {quando(d.created_at)} — {d.failure_reason || "não deu certo"}
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </>
        )}
      </Section>
    </>
  );
}
