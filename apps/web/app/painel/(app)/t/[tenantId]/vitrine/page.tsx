import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Media, Page, ProductSummary } from "@/lib/panel/types";

import { Block as StoreBlock } from "../../../../../(storefront)/_store/landing-blocks";
import storeStyles from "../../../../../(storefront)/_store/store.module.css";
import { themeVariables, type Branding } from "@/lib/theme";

import styles from "../../../../panel.module.css";
import { EmptyState, PageHeader, Section } from "../ui";
import { Flash } from "../flash";
import { ImageUploader } from "../image-uploader";
import { addBlock } from "./actions";
import { BlockCard, BLOCK_LABEL, BLOCK_LABEL_ORDER, type Block } from "./block-form";
import local from "./vitrine.module.css";

export const metadata: Metadata = { title: "Página inicial" };

interface CategorySummary {
  id: string;
  name: string;
}

/** Blocos resolvidos, como a vitrine os recebe. */
type Resolved = Record<string, unknown> & { type: string };

/** O questionário e a torneira, como `GET /landing/brief` devolve. */
interface BriefState {
  steps: string[];
  all_steps: string[];
  usable: boolean;
  quota: { used: number; limit: number; left: number; paid: boolean };
}

export default async function VitrinePage({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string; tela?: string }>;
}) {
  const { tenantId } = await params;
  const me = await requireMe();
  const context = await loadTenantContext(tenantId);
  if (!context) notFound();
  // Sem permissão de configurar, a página não existe para esta conta.
  if (!tenantScopes(me, tenantId).can("settings:write")) notFound();

  const path = `/admin/tenants/${tenantId}`;
  const base = `/t/${tenantId}`;
  const [landingMedia, brandMedia, products, categories, preview, brief] = await Promise.all([
    api<Media[]>(`${path}/media?owner_type=landing`),
    api<Media[]>(`${path}/media?owner_type=tenant_brand`),
    api<Page<ProductSummary>>(`${path}/products?limit=100`),
    api<CategorySummary[]>(`${path}/categories`).catch(() => [] as CategorySummary[]),
    // A prévia vem do mesmo resolver da vitrine: é isto que impede esta tela de mentir.
    api<Resolved[]>(`${path}/landing/preview`).catch(() => [] as Resolved[]),
    api<BriefState>(`${path}/landing/brief`),
  ]);

  const blocks = ((context.settings.landing?.blocks as Block[] | undefined) ?? []).filter(Boolean);
  const branding = (context.settings.branding ?? {}) as Branding;
  const { ok, erro, tela } = await searchParams;
  const celular = tela === "celular";
  const nextStep = brief.all_steps.find((key) => !brief.steps.includes(key)) ?? brief.all_steps[0];

  return (
    <>
      <PageHeader
        eyebrow="Vitrine"
        title="Página inicial"
        lead="Os blocos abaixo são, de cima para baixo, o que o cliente vê ao abrir a sua loja."
      />
      <Flash ok={ok} erro={erro} />

      <Section
        title="Deixe a gente montar para você"
        description={`${brief.steps.length} de ${brief.all_steps.length} respondidos`}
        actions={
          <a className={styles.button} href={`${base}/vitrine/brief/${nextStep}`}>
            {brief.steps.length ? "Continuar respondendo" : "Começar"}
          </a>
        }
      >
        <p className={styles.hint}>
          Conte sobre a sua loja em quatro perguntas curtas e a gente monta uma proposta de página
          inicial para você aprovar. Nada vai para o ar sem o seu OK.
        </p>
        <p className={styles.hint}>
          {brief.quota.paid
            ? `Você tem ${brief.quota.left} de ${brief.quota.limit} propostas neste mês.`
            : `Estão incluídas ${brief.quota.limit} propostas por mês; você já usou ${brief.quota.used}.`}
        </p>
      </Section>

      <Section
        title="Prévia"
        description="O mesmo desenho da loja no ar. Publicar é salvar: o que está aqui já está valendo."
        actions={
          <>
            <a className={styles.buttonSmall} href="?tela=computador">
              Computador
            </a>
            <a className={styles.buttonSmall} href="?tela=celular">
              Celular
            </a>
          </>
        }
      >
        {preview.length === 0 ? (
          <EmptyState title="A sua página inicial ainda está vazia.">
            Quem abrir a loja vê só o nome dela. Comece por um destaque, logo abaixo.
          </EmptyState>
        ) : (
          <div className={celular ? `${local.canvas} ${local.canvasPhone}` : local.canvas}>
            {/* A prévia declara as variáveis da marca e redesenha os títulos: o painel tem os
                próprios tamanhos de h1/h2, e sem isto a prévia sairia menor que a loja real. */}
            <div
              className={`${storeStyles.storefront} ${local.canvasInner}`}
              style={themeVariables(branding)}
            >
              {preview.map((block, i) => (
                <StoreBlock
                  key={typeof block.id === "string" ? block.id : i}
                  block={block}
                  first={i === 0}
                  // O dono vê tudo: os blocos de catálogo aparecem cheios para ele.
                  context={{ catalogOn: Boolean(context.features.catalog), chatUrl: null }}
                />
              ))}
            </div>
          </div>
        )}
      </Section>

      <Section
        title="Blocos"
        description={`${blocks.length} de 16`}
        actions={
          <form action={addBlock} className={local.addForm}>
            <input type="hidden" name="tenant_id" value={tenantId} />
            <label className={local.addLabel} htmlFor="novo-bloco">
              Acrescentar
            </label>
            <select id="novo-bloco" name="type" defaultValue="hero">
              {BLOCK_LABEL_ORDER.map((type) => (
                <option key={type} value={type}>
                  {BLOCK_LABEL[type] ?? type}
                </option>
              ))}
            </select>
            <button type="submit" className={styles.button} disabled={blocks.length >= 16}>
              Acrescentar
            </button>
          </form>
        }
      >
        {blocks.length === 0 ? (
          <EmptyState title="Nenhum bloco ainda.">
            Um destaque com o nome da loja e um botão já é uma página inicial de verdade.
          </EmptyState>
        ) : (
          <div className={local.blocks}>
            {blocks.map((block, i) => (
              <BlockCard
                key={block.id ?? i}
                block={block}
                index={i}
                total={blocks.length}
                tenantId={tenantId}
                media={landingMedia}
                products={products.items}
                categories={categories}
              />
            ))}
          </div>
        )}
      </Section>

      <Section title="Imagens da página inicial" description="Envie aqui o que os blocos vão usar">
        {landingMedia.length ? (
          <div className={local.gallery}>
            {landingMedia.map((media, i) => (
              <figure key={media.id} className={local.tile}>
                {media.renditions.length ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={media.renditions[media.renditions.length - 1]!.url} alt="" />
                ) : (
                  <div className={local.tilePending}>processando…</div>
                )}
                <figcaption>Imagem {i + 1}</figcaption>
              </figure>
            ))}
          </div>
        ) : null}
        <ImageUploader tenantId={tenantId} ownerType="landing" label="Enviar imagem para a página inicial" />
      </Section>

      <Section title="Marca" description="A cor e a fonte que a loja inteira usa" id="marca">
        <p className={styles.hint}>
          Logo, cores e fonte continuam em <a href={`/t/${tenantId}/configuracoes#marca`}>Configurações</a>.
          {brandMedia.length ? null : " Você ainda não enviou um logo."}
        </p>
      </Section>
    </>
  );
}
