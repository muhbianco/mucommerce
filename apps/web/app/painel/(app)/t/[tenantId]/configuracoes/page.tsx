import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Media } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { deleteMedia, publishLegalDocument, saveBranding, saveSeo } from "../actions";
import { Flash } from "../flash";
import { ImageUploader } from "../image-uploader";
import { PageHeader, Pill, Section } from "../ui";
import local from "./configuracoes.module.css";

export const metadata: Metadata = { title: "Configurações" };

/** Imagem ainda sem miniatura: a situação em palavras de lojista. */
const MEDIA_STATUS: Record<Media["status"], string> = {
  pending: "Enviando…",
  processing: "Processando…",
  ready: "Pronta",
  failed: "Recusada",
};

function thumb(media: Media): string | undefined {
  return media.renditions[media.renditions.length - 1]?.url;
}

/** Miniatura de uma imagem enviada (ou a situação dela, enquanto processa). */
function Thumb({ media }: { media: Media }) {
  const url = thumb(media);
  return url ? (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt="" className={local.tileImage} />
  ) : (
    <div className={local.tileImage}>{MEDIA_STATUS[media.status] ?? media.status}</div>
  );
}

export default async function Settings({
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
  const path = `/admin/tenants/${context.tenant_id}`;
  // As imagens da página inicial e os produtos saíram daqui: quem cuida deles é a tela da
  // página inicial, que tem prévia.
  const [brandMedia, legal] = await Promise.all([
    api<Media[]>(`${path}/media?owner_type=tenant_brand`),
    api<LegalOverview>(`${path}/legal-documents`),
  ]);
  const branding = context.settings.branding ?? {};
  // Mesmo padrão da API (storefront_context): sem configuração, só clientes aprovados.
  const seo = context.settings.seo ?? {};
  const readyBrand = brandMedia.filter((m) => m.status === "ready");
  const tenantField = <input type="hidden" name="tenant_id" value={context.tenant_id} />;
  const published = (iso: string) =>
    new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeZone: context.timezone }).format(new Date(iso));

  const mediaSelect = (name: string, options: Media[], current: unknown) => (
    <select name={name} defaultValue={typeof current === "string" ? current : ""}>
      <option value="">(nenhuma)</option>
      {options.map((media, index) => (
        <option key={media.id} value={media.id}>
          Imagem {index + 1}
          {media.alt ? ` — ${media.alt}` : ""}
        </option>
      ))}
    </select>
  );

  return (
    <>
      <PageHeader
        eyebrow="Configurações"
        title="Como a sua loja aparece"
        lead="Marca, página inicial, Google e os termos que o cliente aceita ao entrar. Cada parte tem o próprio botão de salvar."
      />
      <Flash ok={ok} erro={erro} />
      <nav className={local.jump} aria-label="Partes desta página">
        <a className={local.jumpLink} href="#marca">
          Marca
        </a>
        <a className={local.jumpLink} href="#pagina-inicial">
          Página inicial
        </a>
        <a className={local.jumpLink} href="#google">
          Google e compartilhamento
        </a>
        <a className={local.jumpLink} href="#termos">
          Termos e privacidade
        </a>
      </nav>

      <Section id="marca" title="Marca" description="Logo, cores e fonte da vitrine">
        <p className={local.subhead}>Imagens da marca</p>
        {brandMedia.length ? (
          <div className={local.gallery}>
            {brandMedia.map((media, index) => (
              <div key={media.id} className={local.tile}>
                <Thumb media={media} />
                <span>Imagem {index + 1}</span>
                <form action={deleteMedia}>
                  {tenantField}
                  <input type="hidden" name="back" value="config" />
                  <input type="hidden" name="media_id" value={media.id} />
                  <button type="submit" className={`${styles.buttonDanger} ${styles.buttonSmall}`}>
                    Remover
                  </button>
                </form>
              </div>
            ))}
          </div>
        ) : null}
        <div className={local.uploader}>
          <ImageUploader
            tenantId={context.tenant_id}
            ownerType="tenant_brand"
            label="Enviar logo ou imagem de compartilhamento"
          />
        </div>

        <form action={saveBranding} className={local.part}>
          {tenantField}
          <div className={styles.fields}>
            <label className={styles.field}>
              Logo
              {mediaSelect("logo_media_id", readyBrand, branding.logo_media_id)}
              <span className={styles.fieldHint}>Envie a imagem acima e escolha aqui qual é o logo.</span>
            </label>
            <label className={styles.field}>
              Fonte da loja
              <select name="font" defaultValue={String(branding.font ?? "system")}>
                <option value="system">Padrão do aparelho</option>
                <option value="serif">Serifada (clássica)</option>
                <option value="rounded">Arredondada</option>
              </select>
            </label>
            <label className={styles.field}>
              Cor principal
              <input
                type="color"
                name="primary_color"
                className={local.color}
                defaultValue={String(branding.primary_color ?? "#111111")}
              />
            </label>
            <label className={styles.field}>
              Cor de destaque (links)
              <input
                type="color"
                name="secondary_color"
                className={local.color}
                defaultValue={String(branding.secondary_color ?? branding.primary_color ?? "#111111")}
              />
            </label>
          </div>
          <div className={styles.formActions}>
            <button type="submit" className={styles.button}>
              Salvar marca
            </button>
          </div>
        </form>
      </Section>

      <Section id="pagina-inicial" title="Página inicial" description="Os blocos que o cliente vê ao abrir a loja">
        <p className={styles.hint}>
          A página inicial agora tem tela própria, com prévia:{" "}
          <a href={`/t/${context.tenant_id}/vitrine`}>abrir a página inicial</a>.
        </p>
      </Section>

      <Section id="google" title="Google e compartilhamento" description="Como a loja aparece nas buscas e nos links enviados">
        <form action={saveSeo}>
          {tenantField}
          <div className={styles.fields}>
            <label className={styles.field}>
              Título
              <input name="title" maxLength={70} defaultValue={String(seo.title ?? "")} />
              <span className={styles.fieldHint}>Até 70 letras.</span>
            </label>
            <label className={styles.field}>
              Imagem de compartilhamento
              {mediaSelect("og_image_media_id", readyBrand, seo.og_image_media_id)}
              <span className={styles.fieldHint}>Aparece quando alguém manda o link da loja. Vem das imagens da marca.</span>
            </label>
            <label className={`${styles.field} ${styles.fieldWide}`}>
              Descrição
              <input name="description" maxLength={160} defaultValue={String(seo.description ?? "")} />
              <span className={styles.fieldHint}>Até 160 letras: o resumo que aparece embaixo do título.</span>
            </label>
            <label className={`${styles.check} ${styles.fieldWide}`}>
              <input type="checkbox" name="indexable" defaultChecked={seo.indexable === true} />
              <span>
                Aparecer no Google
                <span className={styles.fieldHint}> · só vale com a loja aberta ao público</span>
              </span>
            </label>
          </div>
          <div className={styles.formActions}>
            <button type="submit" className={styles.button}>
              Salvar Google e compartilhamento
            </button>
          </div>
        </form>
      </Section>

      <Section id="termos" title="Termos e privacidade" description="O que o cliente aceita ao entrar na loja">
        <p className={local.intro}>
          Cada publicação vira uma nova versão. Quem entra na loja aceita a versão mostrada na tela de login, e o
          aceite fica registrado com data.
        </p>
        {(["terms", "privacy"] as const).map((kind) => {
          const current = legal[kind];
          return (
            <form key={kind} action={publishLegalDocument} className={local.legal}>
              {tenantField}
              <input type="hidden" name="kind" value={kind} />
              <label className={styles.field}>
                <span className={local.legalHead}>
                  {LEGAL_LABEL[kind]}
                  {current ? (
                    <Pill state="live">
                      Versão {current.version} · {published(current.published_at)}
                    </Pill>
                  ) : (
                    <Pill state="pending">Não publicado</Pill>
                  )}
                </span>
                <textarea
                  name="content"
                  rows={8}
                  minLength={20}
                  maxLength={100000}
                  required
                  defaultValue={current?.content ?? ""}
                />
              </label>
              <div className={styles.formActions}>
                <button type="submit" className={styles.buttonGhost}>
                  Publicar {LEGAL_LABEL[kind].toLowerCase()}
                </button>
              </div>
            </form>
          );
        })}
      </Section>
    </>
  );
}

const LEGAL_LABEL = { terms: "Termos de uso", privacy: "Política de privacidade" } as const;

interface LegalDocument {
  kind: "terms" | "privacy";
  version: number;
  content: string;
  published_at: string;
}

interface LegalOverview {
  terms: LegalDocument | null;
  privacy: LegalDocument | null;
}
