import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, ApiError, requireMe } from "@/lib/panel/api";
import { formatMoney, moneyInput, utcToLocalInput } from "@/lib/panel/format";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import {
  type Category,
  type Product,
  PRODUCT_STATUS_LABEL,
  type TagRef,
  STOCK_POLICIES,
} from "@/lib/panel/types";

import styles from "../../../../../panel.module.css";
import {
  deleteMedia,
  setProductStatus,
  setVariantPause,
  updateMediaAlt,
  updateProduct,
  updateVariant,
} from "../../actions";
import { Flash } from "../../flash";
import { ImageUploader } from "../../image-uploader";

export const metadata: Metadata = { title: "Produto" };

export default async function ProductPage({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string; productId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId, productId } = await params;
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!context.features.catalog) notFound();
  const scopes = tenantScopes(me, context.tenant_id);
  const path = `/admin/tenants/${context.tenant_id}`;
  const base = `/t/${encodeURIComponent(context.tenant_id)}`;

  let product: Product;
  try {
    product = await api<Product>(`${path}/products/${encodeURIComponent(productId)}`);
  } catch (error) {
    if (error instanceof ApiError && (error.status === 404 || error.status === 422)) notFound();
    throw error;
  }
  const [categories, tags] = await Promise.all([
    api<Category[]>(`${path}/categories`),
    api<TagRef[]>(`${path}/tags`),
  ]);
  const otherTags = tags.filter((tag) => !product.tags.some((mine) => mine.slug === tag.slug));
  const canWrite = scopes.can("catalog:write") && product.status !== "archived";
  const zone = context.timezone;
  const hidden = (
    <>
      <input type="hidden" name="tenant_id" value={context.tenant_id} />
      <input type="hidden" name="product_id" value={product.id} />
    </>
  );
  const ready = product.media.filter((m) => m.status === "ready").length;
  const published = product.status === "active" || product.status === "paused";
  const canPublish = scopes.can("catalog:publish") && product.status !== "archived";

  return (
    <>
      <p>
        <Link href={`${base}/produtos`}>← Produtos</Link>
      </p>
      <h2>
        {product.name} <span className={styles.badge}>{PRODUCT_STATUS_LABEL[product.status]}</span>
      </h2>
      <Flash ok={ok} erro={erro} />

      {canPublish ? (
        <section className={styles.card}>
          <h2>Vitrine</h2>
          <p>
            Preço atual: {formatMoney(product.price.amount_cents)}
            {product.price.compare_at_cents ? ` (de ${formatMoney(product.price.compare_at_cents)})` : ""} ·
            Imagens prontas: {ready}
            {published && context.primary_host ? (
              <>
                {" · "}
                <a
                  href={`https://${context.primary_host}/loja/produto/${product.slug}`}
                  target="_blank"
                  rel="noreferrer"
                >
                  ver na loja
                </a>
              </>
            ) : null}
          </p>
          <div className={styles.form}>
            <form action={setProductStatus}>
              {hidden}
              <input type="hidden" name="action" value={published ? "unpublish" : "publish"} />
              <button type="submit" className={published ? styles.buttonGhost : styles.button}>
                {published ? "Tirar da vitrine" : "Publicar"}
              </button>
            </form>
            {product.status === "active" ? (
              <form action={setProductStatus}>
                {hidden}
                <input type="hidden" name="action" value="pause" />
                <label>
                  Motivo (só você vê)
                  <input name="reason" maxLength={200} placeholder="ex.: forno em manutenção" />
                </label>
                <button type="submit" className={styles.button}>
                  Pausar venda
                </button>
              </form>
            ) : null}
            {product.status === "paused" ? (
              <form action={setProductStatus}>
                {hidden}
                <input type="hidden" name="action" value="resume" />
                <button type="submit" className={styles.button}>
                  Retomar venda
                </button>
              </form>
            ) : null}
            {scopes.can("catalog:write") ? (
              <form action={setProductStatus}>
                {hidden}
                <input type="hidden" name="action" value="archive" />
                <button type="submit" className={styles.buttonGhost}>
                  Arquivar
                </button>
              </form>
            ) : null}
          </div>
          {product.status === "paused" ? (
            <p>
              Venda pausada{product.paused_reason ? `: ${product.paused_reason}` : ""}. Na vitrine, o produto
              aparece como indisponível.
            </p>
          ) : (
            <p className="muted">Para publicar: preço maior que zero e ao menos uma imagem pronta.</p>
          )}
        </section>
      ) : null}

      <section className={styles.card}>
        <h2>Imagens</h2>
        <div className={styles.flags}>
          {product.media.map((media) => (
            <div key={media.id}>
              {media.renditions.length ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={media.renditions[media.renditions.length - 1]?.url}
                  alt={media.alt ?? ""}
                  width={160}
                  style={{ maxWidth: "100%", height: "auto" }}
                />
              ) : (
                <p>{media.status === "failed" ? `recusada: ${media.failure_reason ?? ""}` : media.status}</p>
              )}
              {canWrite ? (
                <>
                  <form action={updateMediaAlt} className={styles.form}>
                    {hidden}
                    <input type="hidden" name="media_id" value={media.id} />
                    <label>
                      Texto alternativo
                      <input name="alt" defaultValue={media.alt ?? ""} maxLength={300} />
                    </label>
                    <label>
                      Ordem
                      <input name="position" type="number" defaultValue={media.position} style={{ width: "5rem" }} />
                    </label>
                    <button type="submit" className={styles.buttonGhost}>
                      Salvar imagem
                    </button>
                  </form>
                  <form action={deleteMedia}>
                    {hidden}
                    <input type="hidden" name="media_id" value={media.id} />
                    <button type="submit" className={styles.buttonGhost}>
                      Remover
                    </button>
                  </form>
                </>
              ) : null}
            </div>
          ))}
        </div>
        {canWrite && scopes.can("media:write") ? (
          <ImageUploader tenantId={context.tenant_id} ownerType="product" ownerId={product.id} />
        ) : null}
      </section>

      <section className={styles.card}>
        <h2>Dados</h2>
        <form action={updateProduct} className={styles.form}>
          {hidden}
          <input type="hidden" name="time_zone" value={zone} />
          <fieldset disabled={!canWrite} style={{ display: "contents" }}>
            <label>
              Nome
              <input name="name" required maxLength={200} defaultValue={product.name} />
            </label>
            <label>
              Endereço (slug)
              <input name="slug" required maxLength={160} pattern="[a-z0-9]+(-[a-z0-9]+)*" defaultValue={product.slug} />
            </label>
            <label>
              Preço (R$)
              <input name="price" required inputMode="decimal" defaultValue={moneyInput(product.base_price_cents)} />
            </label>
            <label>
              Preço promocional
              <input name="promo_price" inputMode="decimal" defaultValue={moneyInput(product.promo_price_cents)} />
            </label>
            <label>
              Promoção de ({zone})
              <input name="promo_starts_at" type="datetime-local" defaultValue={utcToLocalInput(product.promo_starts_at, zone)} />
            </label>
            <label>
              até
              <input name="promo_ends_at" type="datetime-local" defaultValue={utcToLocalInput(product.promo_ends_at, zone)} />
            </label>
            <label>
              Custo estimado
              <input name="cost" inputMode="decimal" defaultValue={moneyInput(product.cost_cents_estimate)} />
            </label>
            <label>
              Estoque
              <select name="stock_policy" defaultValue={product.stock_policy}>
                {Object.entries(STOCK_POLICIES).map(([value, label]) => (
                  <option key={value} value={value}>
                    {label}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Vendido por
              <select name="sold_by" defaultValue={product.sold_by}>
                <option value="unit">unidade</option>
                <option value="weight">peso</option>
              </select>
            </label>
            <label>
              Unidade
              <input name="unit_label" maxLength={16} defaultValue={product.unit_label} style={{ width: "5rem" }} />
            </label>
            <label>
              Ordem na vitrine
              <input name="position" type="number" defaultValue={product.position} style={{ width: "6rem" }} />
            </label>
            <label style={{ flexBasis: "100%" }}>
              Resumo
              <input name="short_description" maxLength={500} defaultValue={product.short_description ?? ""} />
            </label>
            <label style={{ flexBasis: "100%" }}>
              Descrição
              <textarea name="description_md" rows={6} maxLength={20000} defaultValue={product.description_md ?? ""} />
            </label>
            <fieldset style={{ flexBasis: "100%" }}>
              <legend>Categorias</legend>
              <div className={styles.flags}>
                {categories.map((category) => (
                  <label key={category.id}>
                    <input
                      type="checkbox"
                      name="category_ids"
                      value={category.id}
                      defaultChecked={product.category_ids.includes(category.id)}
                    />
                    {category.parent_id ? "— " : ""}
                    {category.name}
                  </label>
                ))}
              </div>
            </fieldset>
            <label style={{ flexBasis: "100%" }}>
              Tags (separadas por vírgula; filtram a vitrine)
              <input
                name="tags"
                maxLength={1300}
                defaultValue={product.tags.map((tag) => tag.name).join(", ")}
                placeholder="ex.: vegano, sem glúten"
              />
            </label>
            {otherTags.length ? (
              <p className="muted" style={{ flexBasis: "100%" }}>
                Já usadas na loja: {otherTags.slice(0, 30).map((tag) => tag.name).join(", ")}
              </p>
            ) : null}
            <label>
              Título para buscadores
              <input name="seo_title" maxLength={70} defaultValue={product.seo?.title ?? ""} />
            </label>
            <label style={{ flexBasis: "100%" }}>
              Descrição para buscadores
              <input name="seo_description" maxLength={160} defaultValue={product.seo?.description ?? ""} />
            </label>
            {canWrite ? (
              <button type="submit" className={styles.button}>
                Salvar dados
              </button>
            ) : null}
          </fieldset>
        </form>
      </section>

      <section className={styles.card}>
        <h2>Variante e estoque</h2>
        {product.variants.map((variant) => (
          <div key={variant.id}>
            <form action={updateVariant} className={styles.form}>
              {hidden}
              <input type="hidden" name="variant_id" value={variant.id} />
              <span>
                {variant.name} · {variant.sku}
                {variant.status === "paused" ? (
                  <>
                    {" "}
                    <span className={styles.badge}>pausada</span>
                    {variant.paused_reason ? ` ${variant.paused_reason}` : ""}
                  </>
                ) : null}
              </span>
              <label>
                Preço próprio (vazio = do produto)
                <input name="price" inputMode="decimal" defaultValue={moneyInput(variant.price_cents)} disabled={!canWrite} />
              </label>
              <label>
                Custo
                <input name="cost" inputMode="decimal" defaultValue={moneyInput(variant.cost_cents)} disabled={!canWrite} />
              </label>
              {canWrite ? (
                <button type="submit" className={styles.buttonGhost}>
                  Salvar variante
                </button>
              ) : null}
              {context.features.inventory && product.stock_policy === "tracked" ? (
                <Link href={`${base}/estoque/${variant.id}`}>Estoque e extrato →</Link>
              ) : null}
            </form>
            {canPublish && (variant.status === "active" || variant.status === "paused") ? (
              <form action={setVariantPause} className={styles.form}>
                {hidden}
                <input type="hidden" name="variant_id" value={variant.id} />
                <input type="hidden" name="action" value={variant.status === "paused" ? "resume" : "pause"} />
                {variant.status === "active" ? (
                  <label>
                    Motivo da pausa
                    <input name="reason" maxLength={200} />
                  </label>
                ) : null}
                <button type="submit" className={styles.buttonGhost}>
                  {variant.status === "paused" ? "Retomar variante" : "Pausar variante"}
                </button>
              </form>
            ) : null}
          </div>
        ))}
      </section>
    </>
  );
}
