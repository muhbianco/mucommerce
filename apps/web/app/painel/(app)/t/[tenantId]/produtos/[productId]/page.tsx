import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, ApiError, requireMe } from "@/lib/panel/api";
import { formatMoney, moneyInput, utcToLocalInput } from "@/lib/panel/format";
import { tenantScopes } from "@/lib/panel/scopes";
import { type PillState, PRODUCT_STATE, stateOf } from "@/lib/panel/states";
import { loadTenantContext } from "@/lib/panel/tenant-context";

/** Uma embalagem da loja, como a setting de envio a guarda. */
interface Caixa {
  id?: string;
  name?: string;
  width_mm: number;
  height_mm: number;
  depth_mm: number;
  max_weight_grams?: number;
  empty_weight_grams?: number;
}

/** Cabe na caixa girando a peça? Compara as três medidas ordenadas, como o empacotador faz. */
function cabeGirando(peca: number[], caixa: number[]): boolean {
  const p = [...peca].sort((a, b) => a - b);
  const c = [...caixa].sort((a, b) => a - b);
  return p.every((mm, i) => mm <= (c[i] ?? 0));
}
import {
  type Category,
  type EventLot,
  EVENT_STATUS_LABEL,
  LOT_STATE_LABEL,
  type Media,
  MODIFIER_GROUP_ROWS,
  type ModifierGroup,
  type Product,
  PRODUCT_KINDS,
  PRODUCT_OPTION_ROWS,
  PRODUCT_STATUS_LABEL,
  type ProductEvent,
  type TagRef,
  STOCK_POLICIES,
} from "@/lib/panel/types";

import styles from "../../../../../panel.module.css";
import {
  deleteMedia,
  removeLot,
  saveEvent,
  saveLot,
  setProductModifiers,
  setProductOptions,
  setProductStatus,
  setVariantPause,
  updateMediaAlt,
  updateProduct,
  updateVariant,
} from "../../actions";
import { Flash } from "../../flash";
import { ImageUploader } from "../../image-uploader";
import { EmptyState, KeyValues, PageHeader, Pill, Section } from "../../ui";
import local from "./produto.module.css";

export const metadata: Metadata = { title: "Produto" };

/** Frase sobre a situação do produto na loja (a de pausado leva o motivo e é montada à parte). */
const STATUS_NOTE: Record<string, string> = {
  draft: "Rascunho: só aparece aqui no painel.",
  active: "Publicado: aparece na loja.",
  inactive: "Fora da vitrine: o cliente não vê.",
  archived: "Arquivado: fica só para consulta.",
};

/** Foto ainda sem versão pronta: a situação em palavras de lojista. */
const MEDIA_STATE: Record<Media["status"], { label: string; state: PillState }> = {
  pending: { label: "Aguardando envio", state: "pending" },
  processing: { label: "Processando", state: "pending" },
  ready: { label: "Pronta", state: "live" },
  failed: { label: "Recusada", state: "warn" },
};

const LOT_STATE: Record<EventLot["state"], PillState> = {
  on_sale: "live",
  upcoming: "pending",
  sold_out: "off",
  ended: "off",
  unavailable: "warn",
};

/** "fora de venda (produto não publicado…)" → selo curto e a explicação embaixo dele. */
function lotState(state: EventLot["state"]): { label: string; detail: string | null } {
  const text = LOT_STATE_LABEL[state] ?? state;
  const match = /^(.*?)\s*\((.*)\)$/.exec(text);
  return match ? { label: match[1] || text, detail: match[2] || null } : { label: text, detail: null };
}

/** Categorias na ordem da árvore: a principal e, logo depois, as de dentro dela. Nenhuma fica de fora. */
function categoryTree(categories: Category[]): { category: Category; parent: Category | null }[] {
  const byId = new Map(categories.map((category) => [category.id, category]));
  const rows: { category: Category; parent: Category | null }[] = [];
  for (const root of categories.filter((category) => !category.parent_id)) {
    rows.push({ category: root, parent: null });
    for (const child of categories.filter((category) => category.parent_id === root.id)) {
      rows.push({ category: child, parent: root });
    }
  }
  // Sem a principal na lista: vai para o fim, para a marcação do produto não se perder ao salvar.
  const placed = new Set(rows.map((row) => row.category.id));
  for (const category of categories) {
    if (!placed.has(category.id)) {
      rows.push({ category, parent: category.parent_id ? (byId.get(category.parent_id) ?? null) : null });
    }
  }
  return rows;
}

/** Um grupo de adicionais do formulário (os nomes dos campos seguem o índice do grupo). */
function ModifierGroupFields({
  index,
  group,
  disabled,
}: {
  index: number;
  group: ModifierGroup | undefined;
  disabled: boolean;
}) {
  return (
    <fieldset className={local.group} disabled={disabled}>
      <legend>Grupo {index + 1}</legend>
      <div className={local.groupFields}>
        <label className={styles.field}>
          Nome do grupo
          <input
            name={`group_name_${index}`}
            maxLength={60}
            defaultValue={group?.name ?? ""}
            placeholder={index === 0 ? "ex.: Cobertura" : undefined}
          />
        </label>
        <label className={styles.field}>
          Mínimo
          <input name={`group_min_${index}`} type="number" min={0} max={30} defaultValue={group?.min_select ?? 0} />
          <span className={styles.fieldHint}>0 = opcional</span>
        </label>
        <label className={styles.field}>
          Máximo
          <input name={`group_max_${index}`} type="number" min={1} max={30} defaultValue={group?.max_select ?? 1} />
          <span className={styles.fieldHint}>Quantos dá para escolher</span>
        </label>
        <label className={styles.field}>
          Adicionais
          <textarea
            name={`group_items_${index}`}
            rows={3}
            maxLength={3000}
            placeholder={index === 0 ? "Chocolate = 3,50\nGranulado" : undefined}
            defaultValue={(group?.modifiers ?? [])
              .filter((m) => m.active)
              .map((m) => (m.price_cents ? `${m.name} = ${moneyInput(m.price_cents)}` : m.name))
              .join("\n")}
          />
          <span className={styles.fieldHint}>Um por linha: Nome = 3,50</span>
        </label>
      </div>
    </fieldset>
  );
}

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
  // A caixa padrão da loja, para avisar quando o produto não cabe nela. Espelha
  // `app/shipping/packing.py#Box.fits`: compara as medidas ordenadas (o item pode girar) e o
  // peso. Os dois lados precisam concordar, senão o aviso mente.
  const embalagens = (
    (context.settings.fulfillment as { shipping?: { boxes?: Caixa[] } } | undefined)?.shipping
      ?.boxes ?? []
  ).filter((c): c is Caixa & { id: string } => Boolean(c?.id && c?.name));
  // O aviso compara com a embalagem que o produto escolheu, não com a padrão: senão ele mentiria
  // exatamente para quem resolveu o problema atrelando uma caixa maior.
  const escolhida = embalagens.find((c) => c.id === product.shipping_box_id) ?? null;
  const caixa = escolhida ?? (
    (context.settings.fulfillment as { shipping?: { box?: Record<string, number> } } | undefined)
      ?.shipping?.box ?? null
  ) as Partial<Caixa> | null;
  const medidas = [product.width_mm, product.height_mm, product.depth_mm];
  const temMedidas = medidas.every((mm): mm is number => typeof mm === "number" && mm > 0);
  const caixaMm = caixa ? [caixa.width_mm, caixa.height_mm, caixa.depth_mm] : [];
  const temCaixa = caixaMm.every((mm): mm is number => typeof mm === "number" && mm > 0);
  // Peso **útil**: o teto menos a embalagem vazia. É a mesma conta de `Box.usable_grams`; usar o
  // teto cheio aqui daria "cabe" para uma peça que o empacotador manda viajar sozinha.
  const pesoUtil = Math.max(0, (caixa?.max_weight_grams ?? 0) - (caixa?.empty_weight_grams ?? 0));
  const soltoNaCotacao =
    temMedidas &&
    temCaixa &&
    (!cabeGirando(medidas as number[], caixaMm as number[]) ||
      (product.weight_grams ?? 0) > pesoUtil);
  const caixaNome = escolhida?.name ? `embalagem “${escolhida.name}”` : "caixa padrão";
  const caixaResumo = temCaixa ? `${caixaMm.map((mm) => (mm as number) / 10).join(" × ")} cm` : "";
  // Peso cúbico da transportadora: comprimento × largura × altura em cm, dividido por 6000.
  const cubadoKg = temMedidas
    ? ((medidas as number[]).reduce((a, b) => a * (b / 10), 1) / 6000).toFixed(1).replace(".", ",")
    : "";

  const otherTags = tags.filter((tag) => !product.tags.some((mine) => mine.slug === tag.slug));
  const isTicket = product.kind === "ticket";
  let event: ProductEvent | null = null;
  if (isTicket && context.features.events) {
    try {
      event = await api<ProductEvent>(`${path}/products/${encodeURIComponent(product.id)}/event`);
    } catch (error) {
      if (!(error instanceof ApiError && error.status === 404)) throw error;
    }
  }
  const canWrite = scopes.can("catalog:write") && product.status !== "archived";
  const tiers = (product.price_tiers ?? []) as { min_qty_milli: number; unit_price_cents: number }[];
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
  const canArchive = canPublish && scopes.can("catalog:write");
  const hasEvent = isTicket && context.features.events;
  const lotPrices = (event?.lots ?? []).map((lot) => lot.price_cents);
  const tree = categoryTree(categories);
  const storeUrl =
    published && context.primary_host ? `https://${context.primary_host}/loja/produto/${product.slug}` : null;
  const statusNote =
    product.status === "paused"
      ? `Venda pausada${product.paused_reason ? `: ${product.paused_reason}` : ""}. Na vitrine, o produto aparece como indisponível.`
      : (STATUS_NOTE[product.status] ?? null);
  // Grupos de adicionais: os preenchidos e um vazio à vista; os outros vazios ficam em "Mais grupos".
  const usedGroups = Math.min(MODIFIER_GROUP_ROWS, product.modifier_groups.length);
  const shownGroups = canWrite ? Math.min(MODIFIER_GROUP_ROWS, usedGroups + 1) : usedGroups;
  const smallButton = `${styles.buttonGhost} ${styles.buttonSmall}`;
  const dangerButton = `${styles.buttonDanger} ${styles.buttonSmall}`;
  const toc: [string, string][] = [
    ["fotos", "Fotos"],
    ["basico", "Informações básicas"],
    ["preco", "Preço"],
    ["venda", "Como você vende"],
    ["organizacao", "Organização na loja"],
    ["link", "Link e Google"],
    ...(isTicket ? [] : ([["opcoes", "Opções"]] as [string, string][])),
    ...(hasEvent ? ([["evento", "Evento e lotes"]] as [string, string][]) : []),
    ["variantes", product.has_variants ? "Variantes e estoque" : "Variante e estoque"],
    ["adicionais", "Adicionais"],
  ];

  return (
    <>
      <PageHeader
        eyebrow="Produtos"
        title={product.name}
        lead="Complete as fotos e os dados e publique quando estiver pronto."
        actions={
          <Link href={`${base}/produtos`} className={smallButton}>
            ← Todos os produtos
          </Link>
        }
      />
      <Flash ok={ok} erro={erro} />

      <div className={`${styles.split} ${local.editor}`}>
        <aside className={local.aside}>
          <Section
            title="Na loja"
            actions={
              <Pill state={stateOf(PRODUCT_STATE, product.status)}>
                {PRODUCT_STATUS_LABEL[product.status] ?? product.status}
              </Pill>
            }
          >
            {statusNote ? <p className={local.statusNote}>{statusNote}</p> : null}
            <KeyValues
              items={[
                isTicket
                  ? {
                      // Ingresso vende pelo preço do lote; o preço do produto não chega ao cliente.
                      label: "Ingressos",
                      value: lotPrices.length ? (
                        `a partir de ${formatMoney(Math.min(...lotPrices))}`
                      ) : (
                        <a href="#evento">Nenhum lote</a>
                      ),
                    }
                  : {
                      label: "Preço agora",
                      value: (
                        <>
                          {formatMoney(product.price.amount_cents)}
                          {product.price.compare_at_cents ? (
                            <span className={local.was}>
                              {" "}
                              de <s>{formatMoney(product.price.compare_at_cents)}</s>
                            </span>
                          ) : null}
                        </>
                      ),
                    },
                { label: "Fotos prontas", value: ready ? String(ready) : <a href="#fotos">Nenhuma</a> },
              ]}
            />
            {storeUrl ? (
              <p className={local.storeLink}>
                <a href={storeUrl} target="_blank" rel="noreferrer">
                  Ver na loja ↗
                </a>
              </p>
            ) : null}
            {!published && product.status !== "archived" ? (
              <p className={styles.hint}>
                {isTicket
                  ? "Para publicar: ao menos um lote no evento e uma foto pronta."
                  : "Para publicar: preço maior que zero e ao menos uma foto pronta."}
              </p>
            ) : null}
            {canPublish ? (
              <div className={local.statusActions}>
                {product.status === "paused" ? (
                  <form action={setProductStatus}>
                    {hidden}
                    <input type="hidden" name="action" value="resume" />
                    <button type="submit" className={styles.button}>
                      Retomar venda
                    </button>
                  </form>
                ) : null}
                {published ? null : (
                  <form action={setProductStatus}>
                    {hidden}
                    <input type="hidden" name="action" value="publish" />
                    <button type="submit" className={styles.button}>
                      Publicar
                    </button>
                  </form>
                )}
                {product.status === "active" ? (
                  <form action={setProductStatus}>
                    {hidden}
                    <input type="hidden" name="action" value="pause" />
                    <label className={styles.field}>
                      Motivo (só você vê)
                      <input name="reason" maxLength={200} placeholder="ex.: forno em manutenção" />
                    </label>
                    <button type="submit" className={styles.buttonGhost}>
                      Pausar venda
                    </button>
                    <span className={styles.fieldHint}>O produto continua na vitrine, mas indisponível.</span>
                  </form>
                ) : null}
                {published ? (
                  <form action={setProductStatus}>
                    {hidden}
                    <input type="hidden" name="action" value="unpublish" />
                    <button type="submit" className={styles.buttonGhost}>
                      Tirar da vitrine
                    </button>
                    <span className={styles.fieldHint}>Some da loja até você publicar de novo.</span>
                  </form>
                ) : null}
              </div>
            ) : null}
          </Section>

          <nav className={`${styles.card} ${local.toc}`} aria-label="Partes desta página">
            <p className={local.tocTitle}>Nesta página</p>
            <ol>
              {toc.map(([id, label]) => (
                <li key={id}>
                  <a href={`#${id}`}>{label}</a>
                </li>
              ))}
            </ol>
          </nav>
        </aside>

        <div className={local.main}>
          <Section
            id="fotos"
            title="Fotos"
            description={product.media.length ? (ready === 1 ? "1 foto pronta" : `${ready} fotos prontas`) : undefined}
          >
            {product.media.length ? (
              <ul className={local.photos}>
                {product.media.map((media) => {
                  const view = media.renditions[media.renditions.length - 1];
                  const state: { label: string; state: PillState } = MEDIA_STATE[media.status] ?? {
                    label: media.status,
                    state: "off",
                  };
                  return (
                    <li key={media.id} className={local.photo}>
                      {view ? (
                        // eslint-disable-next-line @next/next/no-img-element
                        <img className={local.photoImg} src={view.url} alt={media.alt ?? ""} />
                      ) : (
                        <div className={local.photoPending}>
                          <Pill state={state.state}>{state.label}</Pill>
                          {media.status === "failed" && media.failure_reason ? (
                            <span>{media.failure_reason}</span>
                          ) : null}
                        </div>
                      )}
                      <div className={local.photoMeta}>
                        <span>Ordem {media.position}</span>
                        {canWrite ? (
                          <details>
                            <summary>Editar</summary>
                            <form action={updateMediaAlt} className={local.photoForm}>
                              {hidden}
                              <input type="hidden" name="media_id" value={media.id} />
                              <label className={styles.field}>
                                Descrição
                                <input name="alt" defaultValue={media.alt ?? ""} maxLength={300} />
                                <span className={styles.fieldHint}>Para leitor de tela e Google.</span>
                              </label>
                              <label className={styles.field}>
                                Ordem
                                <input name="position" type="number" defaultValue={media.position} />
                              </label>
                              <button type="submit" className={smallButton}>
                                Salvar foto
                              </button>
                            </form>
                            <form action={deleteMedia} className={local.photoRemove}>
                              {hidden}
                              <input type="hidden" name="media_id" value={media.id} />
                              <button type="submit" className={dangerButton}>
                                Remover foto
                              </button>
                            </form>
                          </details>
                        ) : null}
                      </div>
                    </li>
                  );
                })}
              </ul>
            ) : (
              <EmptyState title="Nenhuma foto ainda">
                Para publicar, o produto precisa de ao menos uma foto pronta.
              </EmptyState>
            )}
            {canWrite && scopes.can("media:write") ? (
              <div className={local.upload}>
                <ImageUploader
                  tenantId={context.tenant_id}
                  ownerType="product"
                  ownerId={product.id}
                  label="Adicionar fotos"
                />
                <p className={local.uploadHint}>JPEG, PNG ou WebP, até 10 MB cada. Dá para escolher várias de uma vez.</p>
              </div>
            ) : null}
          </Section>

          {/* Um formulário só: a API recebe todos estes campos juntos (PATCH do produto inteiro). */}
          <form action={updateProduct}>
            {hidden}
            <input type="hidden" name="time_zone" value={zone} />
            <fieldset disabled={!canWrite} className={local.plain}>
              <Section id="basico" title="Informações básicas">
                <div className={styles.fields}>
                  <label className={`${styles.field} ${styles.fieldWide}`}>
                    Nome
                    <input name="name" required maxLength={200} defaultValue={product.name} />
                  </label>
                  <label className={`${styles.field} ${styles.fieldWide}`}>
                    Resumo
                    <input name="short_description" maxLength={500} defaultValue={product.short_description ?? ""} />
                    <span className={styles.fieldHint}>Uma frase que aparece logo abaixo do preço.</span>
                  </label>
                  <label className={`${styles.field} ${styles.fieldWide}`}>
                    Descrição
                    <textarea
                      name="description_md"
                      rows={6}
                      maxLength={20000}
                      defaultValue={product.description_md ?? ""}
                    />
                    <span className={styles.fieldHint}>O texto completo da página do produto.</span>
                  </label>
                </div>
              </Section>

              <Section id="preco" title="Preço">
                <div className={styles.fields}>
                  <label className={styles.field}>
                    Preço (R$)
                    <input
                      name="price"
                      required
                      inputMode="decimal"
                      defaultValue={moneyInput(product.base_price_cents)}
                    />
                    {isTicket ? (
                      <span className={styles.fieldHint}>No ingresso, o cliente paga o preço de cada lote.</span>
                    ) : null}
                  </label>
                  <label className={styles.field}>
                    Custo estimado (R$)
                    <input name="cost" inputMode="decimal" defaultValue={moneyInput(product.cost_cents_estimate)} />
                    <span className={styles.fieldHint}>Quanto custa para você. Não aparece na loja.</span>
                  </label>
                </div>
                <h4 className={local.subhead}>Desconto por quantidade</h4>
                <p className={styles.fieldHint}>
                  Quem leva mais paga menos por unidade. O desconto olha o total do produto no
                  carrinho, some ou não em linhas separadas, e cada faixa precisa custar menos que
                  a anterior. Deixe vazio para preço único.
                </p>
                <div className={styles.fields}>
                  {[0, 1, 2].map((i) => {
                    const tier = tiers[i];
                    return (
                      <label key={i} className={styles.field}>
                        {i === 0 ? "A partir de (unidades)" : `Faixa ${i + 1} — a partir de`}
                        <input
                          name={`tier_qty_${i}`}
                          type="number"
                          min={1}
                          defaultValue={tier ? Math.round(tier.min_qty_milli / 1000) : ""}
                        />
                        <span className={styles.fieldHint}>Preço por unidade nesta faixa (R$)</span>
                        <input
                          name={`tier_price_${i}`}
                          inputMode="decimal"
                          defaultValue={tier ? moneyInput(tier.unit_price_cents) : ""}
                        />
                      </label>
                    );
                  })}
                </div>
                <h4 className={local.subhead}>Promoção</h4>
                <div className={styles.fields}>
                  <label className={styles.field}>
                    Preço promocional (R$)
                    <input
                      name="promo_price"
                      inputMode="decimal"
                      defaultValue={moneyInput(product.promo_price_cents)}
                    />
                    <span className={styles.fieldHint}>Menor que o preço. Vazio = sem promoção.</span>
                  </label>
                  <label className={styles.field}>
                    Começa em
                    <input
                      name="promo_starts_at"
                      type="datetime-local"
                      defaultValue={utcToLocalInput(product.promo_starts_at, zone)}
                    />
                    <span className={styles.fieldHint}>Vazio = já vale.</span>
                  </label>
                  <label className={styles.field}>
                    Termina em
                    <input
                      name="promo_ends_at"
                      type="datetime-local"
                      defaultValue={utcToLocalInput(product.promo_ends_at, zone)}
                    />
                    <span className={styles.fieldHint}>Vazio = sem data para acabar.</span>
                  </label>
                </div>
                <p className={styles.hint}>Datas no horário da loja ({zone}).</p>
              </Section>

              <Section id="venda" title="Como você vende">
                <div className={styles.fields}>
                  <label className={styles.field}>
                    Tipo
                    <select name="kind" defaultValue={product.kind}>
                      {Object.entries(PRODUCT_KINDS).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                    {context.features.events && !isTicket ? (
                      <span className={styles.fieldHint}>
                        Ingresso de evento libera data, local e lotes depois de salvar.
                      </span>
                    ) : null}
                  </label>
                  <label className={styles.field}>
                    Estoque
                    <select name="stock_policy" defaultValue={product.stock_policy}>
                      {Object.entries(STOCK_POLICIES).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  <label className={styles.field}>
                    Vendido por
                    <select name="sold_by" defaultValue={product.sold_by}>
                      <option value="unit">Unidade</option>
                      <option value="weight">Peso</option>
                    </select>
                  </label>
                  <label className={styles.field}>
                    Unidade
                    <input name="unit_label" maxLength={16} defaultValue={product.unit_label} />
                    <span className={styles.fieldHint}>Ex.: un, kg, dúzia.</span>
                  </label>
                </div>

                <h4 className={local.subhead}>Peso e medidas da caixa</h4>
                {soltoNaCotacao ? (
                  // Item que não cabe na caixa padrão viaja **sozinho**, um volume por unidade:
                  // dez unidades viram dez fretes. É o que transforma um pedido de R$ 300 numa
                  // cotação de mil e pouco, e nada dizia isso à lojista.
                  <p className={styles.error} role="status">
                    Este produto não cabe na {caixaNome} ({caixaResumo}), então cada unidade é
                    cotada como um volume separado — dez unidades viram dez fretes. Escolha outra
                    embalagem abaixo, cadastre uma maior em <a href={`${base}/envio`}>Envio</a>, ou
                    confira se as medidas estão em milímetros.
                    {cubadoKg ? ` Hoje cada unidade pesa ${cubadoKg} kg de peso cúbico.` : ""}
                  </p>
                ) : null}
                <p className={styles.fieldHint}>
                  Medidas em <strong>milímetros</strong>: uma caixa de 30 × 20 × 8 cm se escreve
                  300 × 200 × 80. A transportadora cobra pelo volume, e sem as quatro o carrinho
                  não mostra frete nenhum para este produto — nem erro, só a opção sumindo. Deixe
                  vazio se ele não é enviado (serviço, digital, ingresso).
                </p>
                {embalagens.length ? (
                  <label className={styles.field}>
                    Embalagem
                    <select name="shipping_box_id" defaultValue={product.shipping_box_id ?? ""}>
                      <option value="">Caixa padrão da loja</option>
                      {embalagens.map((caixa) => (
                        <option key={caixa.id} value={caixa.id}>
                          {caixa.name} ({[caixa.width_mm, caixa.height_mm, caixa.depth_mm]
                            .map((mm) => mm / 10)
                            .join(" × ")} cm)
                        </option>
                      ))}
                    </select>
                    <span className={styles.fieldHint}>
                      Em qual caixa este produto viaja. O empacotador respeita a medida e o peso
                      dela, e abre outra igual quando enche — então as unidades que cabem juntas
                      viajam juntas. Cadastre em <a href={`${base}/envio`}>Envio</a>.
                    </span>
                  </label>
                ) : null}
                <div className={styles.fields}>
                  <label className={styles.field}>
                    Peso com embalagem (g)
                    <input
                      name="weight_grams"
                      type="number"
                      min={0}
                      max={1000000}
                      defaultValue={product.weight_grams ?? ""}
                    />
                  </label>
                  <label className={styles.field}>
                    Largura (mm)
                    <input
                      name="width_mm"
                      type="number"
                      min={0}
                      max={10000}
                      defaultValue={product.width_mm ?? ""}
                    />
                  </label>
                  <label className={styles.field}>
                    Altura (mm)
                    <input
                      name="height_mm"
                      type="number"
                      min={0}
                      max={10000}
                      defaultValue={product.height_mm ?? ""}
                    />
                  </label>
                  <label className={styles.field}>
                    Profundidade (mm)
                    <input
                      name="depth_mm"
                      type="number"
                      min={0}
                      max={10000}
                      defaultValue={product.depth_mm ?? ""}
                    />
                  </label>
                </div>
              </Section>

              <Section id="organizacao" title="Organização na loja">
                <fieldset className={local.plain}>
                  <legend className={local.legend}>Categorias</legend>
                  {tree.length ? (
                    <div className={local.checks}>
                      {tree.map(({ category, parent }) => (
                        <label key={category.id} className={styles.check}>
                          <input
                            type="checkbox"
                            name="category_ids"
                            value={category.id}
                            defaultChecked={product.category_ids.includes(category.id)}
                          />
                          <span>
                            {parent ? <span className="muted">{parent.name} › </span> : null}
                            {category.name}
                          </span>
                        </label>
                      ))}
                    </div>
                  ) : (
                    <p className={styles.hint}>
                      A loja ainda não tem categorias. <Link href={`${base}/categorias`}>Criar categorias</Link>
                    </p>
                  )}
                </fieldset>
                <div className={`${styles.fields} ${local.afterChecks}`}>
                  <label className={`${styles.field} ${styles.fieldWide}`}>
                    Tags
                    <input
                      name="tags"
                      maxLength={1300}
                      defaultValue={product.tags.map((tag) => tag.name).join(", ")}
                      placeholder="ex.: vegano, sem glúten"
                    />
                    <span className={styles.fieldHint}>
                      Separe por vírgula; viram filtros na vitrine.
                      {otherTags.length
                        ? ` Já usadas na loja: ${otherTags
                            .slice(0, 30)
                            .map((tag) => tag.name)
                            .join(", ")}.`
                        : ""}
                    </span>
                  </label>
                  <label className={styles.field}>
                    Ordem na vitrine
                    <input name="position" type="number" defaultValue={product.position} />
                    <span className={styles.fieldHint}>Menor aparece primeiro.</span>
                  </label>
                </div>
              </Section>

              <Section id="link" title="Link e Google">
                <div className={styles.fields}>
                  <label className={`${styles.field} ${styles.fieldWide}`}>
                    Endereço da página
                    <input
                      name="slug"
                      required
                      maxLength={160}
                      pattern="[a-z0-9]+(-[a-z0-9]+)*"
                      defaultValue={product.slug}
                    />
                    <span className={styles.fieldHint}>
                      Só letras minúsculas, números e hífen. Link atual:{" "}
                      <code>
                        {context.primary_host ?? ""}/loja/produto/{product.slug}
                      </code>
                    </span>
                  </label>
                </div>
                <details className={local.seo} open={Boolean(product.seo?.title || product.seo?.description)}>
                  <summary>Título e descrição no Google (opcional)</summary>
                  <div className={`${styles.fields} ${local.seoFields}`}>
                    <label className={`${styles.field} ${styles.fieldWide}`}>
                      Título para buscadores
                      <input name="seo_title" maxLength={70} defaultValue={product.seo?.title ?? ""} />
                      <span className={styles.fieldHint}>Até 70 caracteres. Vazio = nome do produto e da loja.</span>
                    </label>
                    <label className={`${styles.field} ${styles.fieldWide}`}>
                      Descrição para buscadores
                      <input name="seo_description" maxLength={160} defaultValue={product.seo?.description ?? ""} />
                      <span className={styles.fieldHint}>Até 160 caracteres. Vazio = usa o resumo.</span>
                    </label>
                  </div>
                </details>
              </Section>
            </fieldset>
            {canWrite ? (
              <div className={local.saveBar}>
                <span className={local.saveHint}>Salva de uma vez: informações, preço, venda, organização e link.</span>
                <button type="submit" className={styles.button}>
                  Salvar produto
                </button>
              </div>
            ) : null}
          </form>

          {isTicket ? null : (
            <Section id="opcoes" title="Opções" description="Tamanho, sabor, cor… até 3">
              <p className={local.sectionIntro}>
                Ex.: Tamanho = P, M, G. Cada combinação vira uma variante com código (SKU), preço e estoque próprios,
                até 100 combinações. Tirar um valor arquiva as variantes dele; o histórico de estoque fica guardado.
              </p>
              <form action={setProductOptions}>
                {hidden}
                <fieldset disabled={!canWrite} className={local.plain}>
                  <div className={local.optionRows}>
                    {Array.from({ length: PRODUCT_OPTION_ROWS }, (_, i) => (
                      <div key={i} className={local.optionRow}>
                        <label className={styles.field}>
                          Opção {i + 1}
                          <input
                            name={`option_name_${i}`}
                            maxLength={40}
                            defaultValue={product.options[i]?.name ?? ""}
                            placeholder={i === 0 ? "ex.: Tamanho" : undefined}
                          />
                        </label>
                        <label className={styles.field}>
                          Valores (separados por vírgula)
                          <input
                            name={`option_values_${i}`}
                            maxLength={900}
                            defaultValue={product.options[i]?.values.join(", ") ?? ""}
                            placeholder={i === 0 ? "ex.: P, M, G" : undefined}
                          />
                        </label>
                      </div>
                    ))}
                  </div>
                </fieldset>
                {canWrite ? (
                  <div className={styles.formActions}>
                    <button type="submit" className={styles.button}>
                      Salvar opções
                    </button>
                  </div>
                ) : null}
              </form>
            </Section>
          )}

          {hasEvent ? (
            <Section id="evento" title="Evento e lotes" description="Data, local e ingressos">
              <p className={local.sectionIntro}>
                Cada lote tem preço, quantidade de ingressos e período de vendas próprios.
              </p>
              <form action={saveEvent}>
                {hidden}
                <input type="hidden" name="time_zone" value={zone} />
                <fieldset disabled={!canWrite} className={local.plain}>
                  <div className={styles.fields}>
                    <label className={styles.field}>
                      Começa em
                      <input
                        name="starts_at"
                        type="datetime-local"
                        required
                        defaultValue={utcToLocalInput(event?.starts_at, zone)}
                      />
                      <span className={styles.fieldHint}>Horário da loja ({zone}).</span>
                    </label>
                    <label className={styles.field}>
                      Termina em
                      <input name="ends_at" type="datetime-local" defaultValue={utcToLocalInput(event?.ends_at, zone)} />
                    </label>
                    <label className={styles.field}>
                      Local
                      <input name="venue_name" maxLength={160} defaultValue={event?.venue_name ?? ""} />
                      <span className={styles.fieldHint}>Nome do lugar.</span>
                    </label>
                    <label className={styles.field}>
                      Endereço
                      <input name="venue_address" maxLength={300} defaultValue={event?.venue_address ?? ""} />
                    </label>
                    <label className={styles.field}>
                      Cidade
                      <input name="city" maxLength={120} defaultValue={event?.city ?? ""} />
                    </label>
                    <label className={styles.field}>
                      Link do evento online
                      <input
                        name="online_url"
                        type="url"
                        maxLength={500}
                        placeholder="https://"
                        defaultValue={event?.online_url ?? ""}
                      />
                      <span className={styles.fieldHint}>Só quem comprar recebe.</span>
                    </label>
                    <label className={styles.field}>
                      Capacidade
                      <input name="capacity" type="number" min={1} defaultValue={event?.capacity ?? ""} />
                      <span className={styles.fieldHint}>Vazio = sem limite.</span>
                    </label>
                    <label className={styles.field}>
                      Situação
                      <select name="status" defaultValue={event?.status ?? "scheduled"}>
                        {Object.entries(EVENT_STATUS_LABEL).map(([value, label]) => (
                          <option key={value} value={value}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </label>
                    <label className={`${styles.field} ${styles.fieldWide}`}>
                      Aviso aos clientes
                      <input name="status_note" maxLength={300} defaultValue={event?.status_note ?? ""} />
                      <span className={styles.fieldHint}>Para quando o evento for adiado ou cancelado.</span>
                    </label>
                  </div>
                </fieldset>
                {canWrite ? (
                  <div className={styles.formActions}>
                    {/* Com o evento salvo, a ação principal do bloco passa a ser criar lote. */}
                    <button type="submit" className={event ? styles.buttonGhost : styles.button}>
                      Salvar evento
                    </button>
                  </div>
                ) : null}
              </form>

              <h4 className={local.subhead}>Lotes de ingressos</h4>
              {event ? (
                <>
                  <p className={local.allocation}>
                    Ingressos nos lotes: <strong>{event.allocated}</strong>
                    {event.capacity ? ` de ${event.capacity}` : ""}
                  </p>
                  <p className={styles.hint}>
                    A quantidade vira o estoque do lote; diminuir nunca apaga ingressos já vendidos.
                  </p>
                  {event.lots.length ? (
                    <ul className={local.lots}>
                      {event.lots.map((lot) => {
                        const lotView = lotState(lot.state);
                        return (
                          <li key={lot.id} className={local.lot}>
                            <div className={local.lotHead}>
                              <strong>{lot.name}</strong>
                              <span className={local.lotSku}>{lot.sku}</span>
                              <Pill state={LOT_STATE[lot.state] ?? "off"}>{lotView.label}</Pill>
                              {canWrite ? (
                                <form action={removeLot} className={local.lotRemove}>
                                  {hidden}
                                  <input type="hidden" name="lot_id" value={lot.id} />
                                  <button type="submit" className={dangerButton}>
                                    Remover lote
                                  </button>
                                </form>
                              ) : null}
                            </div>
                            {lotView.detail ? <p className={local.lotDetail}>{lotView.detail}</p> : null}
                            <form action={saveLot}>
                              {hidden}
                              <input type="hidden" name="time_zone" value={zone} />
                              <input type="hidden" name="lot_id" value={lot.id} />
                              <fieldset disabled={!canWrite} className={local.plain}>
                                <div className={local.grid}>
                                  <label className={`${styles.field} ${local.span2}`}>
                                    Nome do lote
                                    <input name="name" required maxLength={80} defaultValue={lot.name} />
                                  </label>
                                  <label className={styles.field}>
                                    Preço (R$)
                                    <input
                                      name="price"
                                      required
                                      inputMode="decimal"
                                      defaultValue={moneyInput(lot.price_cents)}
                                    />
                                  </label>
                                  <label className={styles.field}>
                                    Ingressos
                                    <input name="quantity" type="number" min={0} required defaultValue={lot.quantity} />
                                    <span className={styles.fieldHint}>{lot.available} restantes</span>
                                  </label>
                                  <label className={`${styles.field} ${local.span2}`}>
                                    Vendas começam
                                    <input
                                      name="sales_starts_at"
                                      type="datetime-local"
                                      defaultValue={utcToLocalInput(lot.sales_starts_at, zone)}
                                    />
                                  </label>
                                  <label className={`${styles.field} ${local.span2}`}>
                                    Vendas terminam
                                    <input
                                      name="sales_ends_at"
                                      type="datetime-local"
                                      defaultValue={utcToLocalInput(lot.sales_ends_at, zone)}
                                    />
                                  </label>
                                </div>
                              </fieldset>
                              {canWrite ? (
                                <div className={local.lotActions}>
                                  <button type="submit" className={smallButton}>
                                    Salvar lote
                                  </button>
                                </div>
                              ) : null}
                            </form>
                          </li>
                        );
                      })}
                    </ul>
                  ) : null}
                  {canWrite ? (
                    <form action={saveLot} className={local.newLot}>
                      {hidden}
                      <input type="hidden" name="time_zone" value={zone} />
                      <div className={local.grid}>
                        <label className={`${styles.field} ${local.span2}`}>
                          Novo lote
                          <input name="name" required maxLength={80} placeholder="ex.: 1º lote" />
                        </label>
                        <label className={styles.field}>
                          Preço (R$)
                          <input name="price" required inputMode="decimal" />
                        </label>
                        <label className={styles.field}>
                          Ingressos
                          <input name="quantity" type="number" min={0} required />
                        </label>
                        <label className={`${styles.field} ${local.span2}`}>
                          Vendas começam
                          <input name="sales_starts_at" type="datetime-local" />
                        </label>
                        <label className={`${styles.field} ${local.span2}`}>
                          Vendas terminam
                          <input name="sales_ends_at" type="datetime-local" />
                        </label>
                      </div>
                      <div className={local.lotActions}>
                        <button type="submit" className={styles.button}>
                          Criar lote
                        </button>
                      </div>
                    </form>
                  ) : null}
                </>
              ) : (
                <p className="muted">Salve o evento para criar os lotes.</p>
              )}
            </Section>
          ) : null}

          <Section
            id="variantes"
            title={product.has_variants ? "Variantes e estoque" : "Variante e estoque"}
            description="Preço próprio vazio = usa o do produto"
          >
            <ul className={`${styles.rows} ${local.list}`}>
              {product.variants.map((variant) => (
                <li key={variant.id} className={styles.row}>
                  <div className={local.variantHead}>
                    <span className={local.variantName}>
                      {variant.name} · {variant.sku}
                    </span>
                    {variant.status === "paused" ? <Pill state="warn">Pausada</Pill> : null}
                    {variant.status === "inactive" ? <Pill state="off">Inativa</Pill> : null}
                    {context.features.inventory && product.stock_policy === "tracked" ? (
                      <Link href={`${base}/estoque/${variant.id}`} className={local.stockLink}>
                        Estoque e extrato →
                      </Link>
                    ) : null}
                  </div>
                  {variant.status === "paused" && variant.paused_reason ? (
                    <p className={local.variantReason}>Motivo: {variant.paused_reason}</p>
                  ) : null}
                  <form action={updateVariant} className={local.inlineForm}>
                    {hidden}
                    <input type="hidden" name="variant_id" value={variant.id} />
                    <label className={styles.field}>
                      Preço próprio (R$)
                      <input
                        name="price"
                        inputMode="decimal"
                        defaultValue={moneyInput(variant.price_cents)}
                        disabled={!canWrite}
                      />
                    </label>
                    <label className={styles.field}>
                      Custo (R$)
                      <input
                        name="cost"
                        inputMode="decimal"
                        defaultValue={moneyInput(variant.cost_cents)}
                        disabled={!canWrite}
                      />
                    </label>
                    {canWrite ? (
                      <button type="submit" className={smallButton}>
                        Salvar variante
                      </button>
                    ) : null}
                  </form>
                  {canPublish && (variant.status === "active" || variant.status === "paused") ? (
                    <form action={setVariantPause} className={local.inlineForm}>
                      {hidden}
                      <input type="hidden" name="variant_id" value={variant.id} />
                      <input type="hidden" name="action" value={variant.status === "paused" ? "resume" : "pause"} />
                      {variant.status === "active" ? (
                        <label className={styles.field}>
                          Motivo da pausa
                          <input name="reason" maxLength={200} placeholder="Opcional" />
                        </label>
                      ) : null}
                      <button type="submit" className={smallButton}>
                        {variant.status === "paused" ? "Retomar variante" : "Pausar variante"}
                      </button>
                    </form>
                  ) : null}
                </li>
              ))}
            </ul>
          </Section>

          <Section id="adicionais" title="Adicionais" description="Extras que somam ao preço">
            {shownGroups === 0 ? (
              <EmptyState title="Nenhum adicional neste produto" />
            ) : (
              <>
                <p className={local.sectionIntro}>
                  Ex.: cobertura, embalagem para presente. Um por linha, no formato <code>Nome = 3,50</code>; sem
                  preço, sai de graça. Mínimo 1 torna a escolha obrigatória.
                </p>
                <form action={setProductModifiers}>
                  {hidden}
                  <div className={local.groups}>
                    {Array.from({ length: shownGroups }, (_, i) => (
                      <ModifierGroupFields key={i} index={i} group={product.modifier_groups[i]} disabled={!canWrite} />
                    ))}
                  </div>
                  {canWrite && shownGroups < MODIFIER_GROUP_ROWS ? (
                    <details className={local.more}>
                      <summary>Mais grupos ({MODIFIER_GROUP_ROWS - shownGroups})</summary>
                      <div className={local.groups}>
                        {Array.from({ length: MODIFIER_GROUP_ROWS - shownGroups }, (_, j) => (
                          <ModifierGroupFields
                            key={shownGroups + j}
                            index={shownGroups + j}
                            group={product.modifier_groups[shownGroups + j]}
                            disabled={!canWrite}
                          />
                        ))}
                      </div>
                    </details>
                  ) : null}
                  {canWrite ? (
                    <div className={styles.formActions}>
                      <button type="submit" className={styles.button}>
                        Salvar adicionais
                      </button>
                    </div>
                  ) : null}
                </form>
              </>
            )}
          </Section>

          {canArchive ? (
            <Section title="Arquivar produto" description="Sai da loja e fica só para consulta no painel">
              {/* Dois cliques: arquivado não volta pelo painel (nem publicar, nem editar). */}
              <details className={local.archive}>
                <summary className={smallButton}>Arquivar produto…</summary>
                <form action={setProductStatus} className={local.archiveConfirm}>
                  {hidden}
                  <input type="hidden" name="action" value="archive" />
                  <p className={styles.hint}>
                    O produto sai da loja e deixa de ser editável aqui. Pedidos antigos continuam com ele.
                  </p>
                  <button type="submit" className={dangerButton}>
                    Sim, arquivar
                  </button>
                </form>
              </details>
            </Section>
          ) : null}
        </div>
      </div>
    </>
  );
}
