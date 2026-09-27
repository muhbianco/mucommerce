import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Media, Page, ProductSummary } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { deleteMedia, publishLegalDocument, saveBranding, saveLanding, saveSeo } from "../actions";
import { Flash } from "../flash";
import { ImageUploader } from "../image-uploader";
import { PageHeader, Pill, Section } from "../ui";
import local from "./configuracoes.module.css";

export const metadata: Metadata = { title: "Configurações" };

type Block = Record<string, unknown> & { type: string };

const ACCESS_LABEL: Record<string, string> = {
  public: "aberta a qualquer pessoa",
  login_required: "só com login",
  whitelist: "só para clientes aprovados",
};

const BLOCK_LABEL: Record<string, string> = {
  hero: "Destaque principal",
  featured_products: "Produtos em destaque",
  text: "Texto",
  contact: "Contato",
  categories: "Categorias",
  gallery: "Galeria",
};

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
  const [brandMedia, landingMedia, products, legal] = await Promise.all([
    api<Media[]>(`${path}/media?owner_type=tenant_brand`),
    api<Media[]>(`${path}/media?owner_type=landing`),
    context.features.catalog
      ? api<Page<ProductSummary>>(`${path}/products?status=active&limit=100`)
      : Promise.resolve({ items: [], next_cursor: null }),
    api<LegalOverview>(`${path}/legal-documents`),
  ]);
  const branding = context.settings.branding ?? {};
  // Mesmo padrão da API (storefront_context): sem configuração, só clientes aprovados.
  const accessMode = String(context.settings.storefront?.access_mode ?? "whitelist");
  const seo = context.settings.seo ?? {};
  const blocks = ((context.settings.landing?.blocks as Block[] | undefined) ?? []).filter(
    // The editor handles these types; others (made elsewhere) are kept out of the form.
    (block) => ["hero", "featured_products", "text", "contact"].includes(block.type),
  );
  const readyBrand = brandMedia.filter((m) => m.status === "ready");
  const readyLanding = landingMedia.filter((m) => m.status === "ready");
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

      <Section id="pagina-inicial" title="Página inicial" description="O que o cliente vê ao abrir a loja, bloco por bloco">
        {accessMode !== "public" &&
        blocks.some((block) => block.type === "featured_products" || block.type === "categories") ? (
          <p className={styles.note}>
            A vitrine está {ACCESS_LABEL[accessMode] ?? accessMode}: quem ainda não tem acesso vê os blocos de
            produtos como “entre para ver”, sem os produtos. Para abrir a loja, fale com o suporte.
          </p>
        ) : null}

        <p className={local.subhead}>Imagens da página inicial</p>
        {landingMedia.length ? (
          <div className={local.gallery}>
            {landingMedia.map((media, index) => (
              <div key={media.id} className={local.tile}>
                <Thumb media={media} />
                <span>Imagem {index + 1}</span>
              </div>
            ))}
          </div>
        ) : null}
        <div className={local.uploader}>
          <ImageUploader tenantId={context.tenant_id} ownerType="landing" label="Enviar imagens para a página inicial" />
        </div>

        <form action={saveLanding} className={local.part}>
          {tenantField}
          <input type="hidden" name="block_count" value={blocks.length} />
          <p className={local.subhead}>Blocos, na ordem em que aparecem</p>
          {blocks.length === 0 ? (
            <p className={styles.hint}>A página inicial ainda não tem blocos. Adicione o primeiro abaixo.</p>
          ) : null}
          {blocks.map((block, i) => (
            <fieldset key={i} className={local.block}>
              <legend>
                {i + 1}. {BLOCK_LABEL[block.type] ?? block.type}
              </legend>
              <input type="hidden" name={`b${i}.type`} value={block.type} />
              <div className={styles.fields}>
                {block.type !== "contact" ? (
                  <label className={styles.field}>
                    Título
                    <input name={`b${i}.title`} maxLength={80} defaultValue={String(block.title ?? "")} />
                  </label>
                ) : null}
                {block.type === "hero" ? (
                  <>
                    <label className={styles.field}>
                      Subtítulo
                      <input name={`b${i}.subtitle`} maxLength={200} defaultValue={String(block.subtitle ?? "")} />
                    </label>
                    <label className={styles.field}>
                      Botão
                      <input name={`b${i}.cta_label`} maxLength={30} defaultValue={String(block.cta_label ?? "")} />
                      <span className={styles.fieldHint}>O texto do botão, como “Ver produtos”.</span>
                    </label>
                    <label className={styles.field}>
                      Botão leva para
                      <select name={`b${i}.cta_target`} defaultValue={String(block.cta_target ?? "catalog")}>
                        <option value="catalog">catálogo</option>
                        <option value="chat">atendimento</option>
                      </select>
                    </label>
                  </>
                ) : null}
                {block.type === "hero" || block.type === "text" ? (
                  <label className={styles.field}>
                    Imagem
                    {mediaSelect(`b${i}.media_id`, readyLanding, block.media_id)}
                  </label>
                ) : null}
                {block.type === "text" ? (
                  <label className={`${styles.field} ${styles.fieldWide}`}>
                    Texto
                    <textarea name={`b${i}.body`} rows={4} maxLength={2000} defaultValue={String(block.body ?? "")} />
                  </label>
                ) : null}
                {block.type === "featured_products" ? (
                  <fieldset className={`${styles.fieldWide} ${local.group}`}>
                    <legend>Produtos</legend>
                    <div className={styles.flags}>
                      {products.items.map((product) => (
                        <label key={product.id} className={styles.check}>
                          <input
                            type="checkbox"
                            name={`b${i}.product_ids`}
                            value={product.id}
                            defaultChecked={((block.product_ids as string[] | undefined) ?? []).includes(product.id)}
                          />
                          {product.name}
                        </label>
                      ))}
                    </div>
                  </fieldset>
                ) : null}
                {block.type === "contact" ? (
                  <>
                    <label className={styles.field}>
                      WhatsApp
                      <input
                        name={`b${i}.whatsapp_e164`}
                        inputMode="tel"
                        defaultValue={String(block.whatsapp_e164 ?? "")}
                        placeholder="11 99999-9999"
                      />
                      <span className={styles.fieldHint}>DDD + número.</span>
                    </label>
                    <label className={styles.field}>
                      Instagram
                      <input name={`b${i}.instagram`} defaultValue={String(block.instagram ?? "")} placeholder="nomedaloja" />
                      <span className={styles.fieldHint}>Só o nome do perfil.</span>
                    </label>
                    <label className={styles.field}>
                      E-mail
                      <input
                        name={`b${i}.email`}
                        inputMode="email"
                        defaultValue={String(block.email ?? "")}
                        placeholder="contato@sualoja.com.br"
                      />
                    </label>
                    <label className={`${styles.field} ${styles.fieldWide}`}>
                      Endereço
                      <input name={`b${i}.address`} maxLength={300} defaultValue={String(block.address ?? "")} />
                    </label>
                    <label className={`${styles.field} ${styles.fieldWide}`}>
                      Horário
                      <input name={`b${i}.hours`} maxLength={300} defaultValue={String(block.hours ?? "")} />
                    </label>
                  </>
                ) : null}
              </div>
              <div className={local.blockFoot}>
                <label className={styles.check}>
                  <input type="checkbox" name={`b${i}.remove`} /> Remover este bloco ao salvar
                </label>
              </div>
            </fieldset>
          ))}

          <div className={styles.fields}>
            <label className={styles.field}>
              Adicionar bloco
              <select name="add_block" defaultValue="">
                <option value="">(nenhum)</option>
                <option value="hero">{BLOCK_LABEL.hero}</option>
                <option value="text">{BLOCK_LABEL.text}</option>
                <option value="contact">{BLOCK_LABEL.contact}</option>
                {products.items.length ? <option value="featured_products">{BLOCK_LABEL.featured_products}</option> : null}
              </select>
              <span className={styles.fieldHint}>O bloco novo entra no fim da página quando você salvar.</span>
            </label>
          </div>
          {products.items.length ? (
            <details className={local.part}>
              <summary>Produtos para um novo bloco de destaques</summary>
              <div className={`${styles.flags} ${local.detailsBody}`}>
                {products.items.map((product) => (
                  <label key={product.id} className={styles.check}>
                    <input type="checkbox" name="add_product_ids" value={product.id} />
                    {product.name}
                  </label>
                ))}
              </div>
            </details>
          ) : null}
          <div className={styles.formActions}>
            <button type="submit" className={styles.button}>
              Salvar página inicial
            </button>
          </div>
        </form>
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
