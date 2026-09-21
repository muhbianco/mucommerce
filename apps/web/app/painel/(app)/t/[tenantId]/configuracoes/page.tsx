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

export const metadata: Metadata = { title: "Configurações" };

type Block = Record<string, unknown> & { type: string };

const BLOCK_LABEL: Record<string, string> = {
  hero: "Destaque principal",
  featured_products: "Produtos em destaque",
  text: "Texto",
  contact: "Contato",
  categories: "Categorias",
  gallery: "Galeria",
};

function thumb(media: Media): string | undefined {
  return media.renditions[media.renditions.length - 1]?.url;
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
  const seo = context.settings.seo ?? {};
  const blocks = ((context.settings.landing?.blocks as Block[] | undefined) ?? []).filter(
    // The editor handles these types; others (made elsewhere) are kept out of the form.
    (block) => ["hero", "featured_products", "text", "contact"].includes(block.type),
  );
  const readyBrand = brandMedia.filter((m) => m.status === "ready");
  const readyLanding = landingMedia.filter((m) => m.status === "ready");
  const tenantField = <input type="hidden" name="tenant_id" value={context.tenant_id} />;

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
      <Flash ok={ok} erro={erro} />

      <section className={styles.card}>
        <h2>Imagens da marca</h2>
        <div className={styles.flags}>
          {brandMedia.map((media, index) => (
            <div key={media.id}>
              {thumb(media) ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={thumb(media)} alt="" width={120} style={{ height: "auto" }} />
              ) : (
                <p>{media.status}</p>
              )}
              <p>Imagem {index + 1}</p>
              <form action={deleteMedia}>
                {tenantField}
                <input type="hidden" name="back" value="config" />
                <input type="hidden" name="media_id" value={media.id} />
                <button type="submit" className={styles.buttonGhost}>
                  Remover
                </button>
              </form>
            </div>
          ))}
        </div>
        <ImageUploader tenantId={context.tenant_id} ownerType="tenant_brand" label="Enviar logo ou imagem de compartilhamento" />
      </section>

      <section className={styles.card}>
        <h2>Marca</h2>
        <form action={saveBranding} className={styles.form}>
          {tenantField}
          <label>
            Cor principal
            <input type="color" name="primary_color" defaultValue={String(branding.primary_color ?? "#111111")} />
          </label>
          <label>
            Cor de destaque (links)
            <input
              type="color"
              name="secondary_color"
              defaultValue={String(branding.secondary_color ?? branding.primary_color ?? "#111111")}
            />
          </label>
          <label>
            Fonte da loja
            <select name="font" defaultValue={String(branding.font ?? "system")}>
              <option value="system">Padrão do aparelho</option>
              <option value="serif">Serifada (clássica)</option>
              <option value="rounded">Arredondada</option>
            </select>
          </label>
          <label>
            Logo
            {mediaSelect("logo_media_id", readyBrand, branding.logo_media_id)}
          </label>
          <button type="submit" className={styles.button}>
            Salvar marca
          </button>
        </form>
      </section>

      <section className={styles.card}>
        <h2>Buscadores e compartilhamento</h2>
        <form action={saveSeo} className={styles.form}>
          {tenantField}
          <label>
            Título
            <input name="title" maxLength={70} defaultValue={String(seo.title ?? "")} />
          </label>
          <label style={{ flexBasis: "100%" }}>
            Descrição
            <input name="description" maxLength={160} defaultValue={String(seo.description ?? "")} />
          </label>
          <label>
            Imagem de compartilhamento
            {mediaSelect("og_image_media_id", readyBrand, seo.og_image_media_id)}
          </label>
          <label>
            <span>
              <input type="checkbox" name="indexable" defaultChecked={seo.indexable === true} /> aparecer no Google
              (só vale com a loja aberta ao público)
            </span>
          </label>
          <button type="submit" className={styles.button}>
            Salvar SEO
          </button>
        </form>
      </section>

      <section className={styles.card}>
        <h2>Página inicial</h2>
        <div className={styles.flags}>
          {landingMedia.map((media, index) => (
            <div key={media.id}>
              {thumb(media) ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img src={thumb(media)} alt="" width={120} style={{ height: "auto" }} />
              ) : (
                <p>{media.status}</p>
              )}
              <p>Imagem {index + 1}</p>
            </div>
          ))}
        </div>
        <ImageUploader tenantId={context.tenant_id} ownerType="landing" label="Enviar imagens para a página inicial" />

        <form action={saveLanding}>
          {tenantField}
          <input type="hidden" name="block_count" value={blocks.length} />
          {blocks.map((block, i) => (
            <fieldset key={i} className={styles.card}>
              <legend>
                {i + 1}. {BLOCK_LABEL[block.type] ?? block.type}
              </legend>
              <input type="hidden" name={`b${i}.type`} value={block.type} />
              <div className={styles.form}>
                {block.type !== "contact" ? (
                  <label>
                    Título
                    <input name={`b${i}.title`} maxLength={80} defaultValue={String(block.title ?? "")} />
                  </label>
                ) : null}
                {block.type === "hero" ? (
                  <>
                    <label>
                      Subtítulo
                      <input name={`b${i}.subtitle`} maxLength={200} defaultValue={String(block.subtitle ?? "")} />
                    </label>
                    <label>
                      Botão
                      <input name={`b${i}.cta_label`} maxLength={30} defaultValue={String(block.cta_label ?? "")} />
                    </label>
                    <label>
                      Botão leva para
                      <select name={`b${i}.cta_target`} defaultValue={String(block.cta_target ?? "catalog")}>
                        <option value="catalog">catálogo</option>
                        <option value="chat">atendimento</option>
                      </select>
                    </label>
                  </>
                ) : null}
                {block.type === "hero" || block.type === "text" ? (
                  <label>
                    Imagem
                    {mediaSelect(`b${i}.media_id`, readyLanding, block.media_id)}
                  </label>
                ) : null}
                {block.type === "text" ? (
                  <label style={{ flexBasis: "100%" }}>
                    Texto
                    <textarea name={`b${i}.body`} rows={4} maxLength={2000} defaultValue={String(block.body ?? "")} />
                  </label>
                ) : null}
                {block.type === "featured_products" ? (
                  <div className={styles.flags} style={{ flexBasis: "100%" }}>
                    {products.items.map((product) => (
                      <label key={product.id}>
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
                ) : null}
                {block.type === "contact" ? (
                  <>
                    <label>
                      WhatsApp (+55…)
                      <input name={`b${i}.whatsapp_e164`} defaultValue={String(block.whatsapp_e164 ?? "")} placeholder="+5511999999999" />
                    </label>
                    <label>
                      Instagram
                      <input name={`b${i}.instagram`} defaultValue={String(block.instagram ?? "")} placeholder="sem @" />
                    </label>
                    <label>
                      E-mail
                      <input name={`b${i}.email`} type="email" defaultValue={String(block.email ?? "")} />
                    </label>
                    <label>
                      Endereço
                      <input name={`b${i}.address`} maxLength={300} defaultValue={String(block.address ?? "")} />
                    </label>
                    <label>
                      Horário
                      <input name={`b${i}.hours`} maxLength={300} defaultValue={String(block.hours ?? "")} />
                    </label>
                  </>
                ) : null}
                <label>
                  <span>
                    <input type="checkbox" name={`b${i}.remove`} /> remover bloco
                  </span>
                </label>
              </div>
            </fieldset>
          ))}
          <div className={styles.form}>
            <label>
              Adicionar bloco
              <select name="add_block" defaultValue="">
                <option value="">(nenhum)</option>
                <option value="hero">{BLOCK_LABEL.hero}</option>
                <option value="text">{BLOCK_LABEL.text}</option>
                <option value="contact">{BLOCK_LABEL.contact}</option>
                {products.items.length ? <option value="featured_products">{BLOCK_LABEL.featured_products}</option> : null}
              </select>
            </label>
            {products.items.length ? (
              <details>
                <summary>Produtos para um novo bloco de destaques</summary>
                <div className={styles.flags}>
                  {products.items.map((product) => (
                    <label key={product.id}>
                      <input type="checkbox" name="add_product_ids" value={product.id} />
                      {product.name}
                    </label>
                  ))}
                </div>
              </details>
            ) : null}
            <button type="submit" className={styles.button}>
              Salvar página inicial
            </button>
          </div>
        </form>
      </section>

      <section className={styles.card}>
        <h2>Termos e privacidade</h2>
        <p className="muted">
          Cada publicação vira uma nova versão. Quem entra na loja aceita a versão mostrada na tela de login, e o
          aceite fica registrado com data.
        </p>
        {(["terms", "privacy"] as const).map((kind) => {
          const current = legal[kind];
          return (
            <form key={kind} action={publishLegalDocument} className={styles.form}>
              {tenantField}
              <input type="hidden" name="kind" value={kind} />
              <label>
                {LEGAL_LABEL[kind]}
                {current ? ` (versão ${current.version})` : " (não publicado)"}
                <textarea name="content" rows={8} minLength={20} maxLength={100000} required defaultValue={current?.content ?? ""} />
              </label>
              <button type="submit" className={styles.buttonGhost}>
                Publicar {LEGAL_LABEL[kind].toLowerCase()}
              </button>
            </form>
          );
        })}
      </section>
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
