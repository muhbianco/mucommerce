import type { Media, ProductSummary } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { duplicateBlock, moveBlock, removeBlock, saveBlock } from "./actions";
import local from "./vitrine.module.css";

/**
 * Um bloco da página inicial, com o formulário dele.
 *
 * Cada bloco é um `<form>` próprio: antes a página inteira ia junto a cada salvar, e um campo
 * inválido em qualquer lugar devolvia tudo. Fica dentro de `<details>` para a lista caber na
 * tela — quem tem dez blocos não quer rolar dez formulários abertos.
 *
 * Mover, duplicar e remover são formulários também, cada um com o id do bloco. Funciona com o
 * JavaScript desligado, que é a régua do painel inteiro.
 */

export interface Block {
  id?: string;
  type: string;
  [key: string]: unknown;
}

/**
 * A ordem em que os tipos são oferecidos: a de montar uma página de cima para baixo.
 *
 * Mora aqui, e não junto das ações, porque arquivo `"use server"` só pode exportar função
 * async — mesma razão do `form-kit`.
 */
export const BLOCK_LABEL_ORDER = [
  "hero",
  "announcement",
  "featured_products",
  "categories",
  "benefits",
  "text",
  "gallery",
  "testimonials",
  "faq",
  "hours",
  "contact",
  "cta",
];

export const BLOCK_LABEL: Record<string, string> = {
  hero: "Destaque",
  announcement: "Aviso",
  featured_products: "Produtos em destaque",
  categories: "Categorias",
  benefits: "Por que comprar aqui",
  text: "Texto",
  gallery: "Fotos",
  testimonials: "Depoimentos",
  faq: "Perguntas frequentes",
  hours: "Onde e quando",
  contact: "Contato",
  cta: "Convite final",
};

/** O que cada arranjo faz, em palavras de quem está montando a página. */
const VARIANTS: Record<string, [string, string][]> = {
  hero: [
    ["image_right", "Foto à direita"],
    ["image_left", "Foto à esquerda"],
    ["image_background", "Foto de fundo"],
    ["text_only", "Só texto"],
  ],
  featured_products: [
    ["grid", "Grade"],
    ["carousel_scroll", "Faixa que rola"],
    ["list", "Lista"],
  ],
  categories: [
    ["pills", "Fichas"],
    ["cards", "Cartões"],
    ["columns", "Colunas"],
  ],
  text: [
    ["single", "Uma coluna"],
    ["two_columns", "Duas colunas"],
    ["with_image_side", "Foto ao lado"],
  ],
  gallery: [
    ["grid", "Grade"],
    ["mosaic", "Mosaico"],
    ["strip", "Faixa que rola"],
  ],
  benefits: [
    ["icons_row", "Linha de ícones"],
    ["cards", "Cartões"],
    ["list", "Lista"],
  ],
  testimonials: [
    ["cards", "Cartões"],
    ["quote_single", "Uma citação grande"],
    ["strip", "Faixa que rola"],
  ],
  faq: [
    ["accordion", "Abre e fecha"],
    ["list", "Tudo aberto"],
  ],
  hours: [
    ["table", "Tabela"],
    ["inline", "Em linha"],
  ],
  contact: [
    ["list", "Lista"],
    ["columns", "Colunas"],
  ],
};

const TONES: [string, string][] = [
  ["plain", "Sem fundo"],
  ["soft", "Fundo claro"],
  ["brand", "Cor da marca"],
  ["dark", "Escuro"],
];

const TONE_BLOCKS = new Set(["hero", "announcement", "cta", "text", "benefits"]);

const ICONS = [
  "truck",
  "motorcycle",
  "store",
  "pix",
  "card",
  "installments",
  "whatsapp",
  "clock",
  "shield",
  "leaf",
  "heart",
  "star",
  "gift",
  "tag",
  "box",
  "phone",
  "pin",
  "calendar",
  "sparkles",
  "users",
];

const DIAS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"];

function str(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function list(value: unknown): Record<string, unknown>[] {
  return Array.isArray(value) ? (value as Record<string, unknown>[]) : [];
}

export function BlockCard({
  block,
  index,
  total,
  tenantId,
  media,
  products,
  categories,
}: {
  block: Block;
  index: number;
  total: number;
  tenantId: string;
  media: Media[];
  products: ProductSummary[];
  categories: { id: string; name: string }[];
}) {
  const hidden = <input type="hidden" name="tenant_id" value={tenantId} />;
  const blockId = <input type="hidden" name="block_id" value={block.id ?? ""} />;
  const conhecido = block.type in BLOCK_LABEL;

  return (
    <details className={local.block} open={index === 0}>
      <summary>
        <span className={local.blockName}>
          {index + 1}. {BLOCK_LABEL[block.type] ?? block.type}
        </span>
        {!conhecido ? <span className={styles.badge}>bloco novo</span> : null}
      </summary>

      <div className={local.blockActions}>
        <form action={moveBlock}>
          {hidden}
          {blockId}
          <button type="submit" name="dir" value="up" disabled={index === 0} aria-label="Subir o bloco">
            ↑
          </button>
          <button
            type="submit"
            name="dir"
            value="down"
            disabled={index === total - 1}
            aria-label="Descer o bloco"
          >
            ↓
          </button>
        </form>
        <form action={duplicateBlock}>
          {hidden}
          {blockId}
          <button type="submit" className={styles.buttonSmall}>
            Duplicar
          </button>
        </form>
        <form action={removeBlock}>
          {hidden}
          {blockId}
          <button type="submit" className={styles.buttonDanger}>
            Remover
          </button>
        </form>
      </div>

      {conhecido ? (
        <form action={saveBlock} className={local.blockForm}>
          {hidden}
          {blockId}
          <Fields
            block={block}
            media={media}
            products={products}
            categories={categories}
          />
          <div className={styles.formActions}>
            <button type="submit" className={styles.button}>
              Salvar bloco
            </button>
          </div>
        </form>
      ) : (
        <p className={styles.hint}>
          Este bloco veio de uma versão mais nova do site. Ele continua na sua página; para editar
          aqui, atualize a página do painel daqui a pouco.
        </p>
      )}
    </details>
  );
}

function Fields({
  block,
  media,
  products,
  categories,
}: {
  block: Block;
  media: Media[];
  products: ProductSummary[];
  categories: { id: string; name: string }[];
}) {
  const variants = VARIANTS[block.type];
  const arranjo = variants ? (
    <label className={styles.field}>
      Arranjo
      <select name="variant" defaultValue={str(block.variant) || variants[0]![0]}>
        {variants.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
    </label>
  ) : null;

  const tom = TONE_BLOCKS.has(block.type) ? (
    <label className={styles.field}>
      Fundo
      <select name="tone" defaultValue={str(block.tone) || (block.type === "cta" ? "brand" : "plain")}>
        {TONES.map(([value, label]) => (
          <option key={value} value={value}>
            {label}
          </option>
        ))}
      </select>
    </label>
  ) : null;

  const imagem = (name: string, current: unknown) => (
    <label className={styles.field}>
      Imagem
      <select name={name} defaultValue={str(current)}>
        <option value="">Sem imagem</option>
        {media
          .filter((m) => m.status === "ready")
          .map((m, i) => (
            <option key={m.id} value={m.id}>
              Imagem {i + 1}
              {m.alt ? ` — ${m.alt}` : ""}
            </option>
          ))}
      </select>
    </label>
  );

  switch (block.type) {
    case "hero":
      return (
        <div className={styles.fields}>
          <label className={styles.field}>
            Título
            <input name="title" maxLength={80} required defaultValue={str(block.title)} />
          </label>
          <label className={styles.field}>
            Subtítulo
            <input name="subtitle" maxLength={200} defaultValue={str(block.subtitle)} />
          </label>
          <label className={styles.field}>
            Texto do botão
            <input name="cta_label" maxLength={30} defaultValue={str(block.cta_label)} />
            <span className={styles.fieldHint}>Deixe em branco para não mostrar botão.</span>
          </label>
          <label className={styles.field}>
            O botão leva para
            <select name="cta_target" defaultValue={str(block.cta_target) || "catalog"}>
              <option value="catalog">os produtos</option>
              <option value="chat">o atendimento</option>
            </select>
          </label>
          {imagem("media_id", block.media_id)}
          {arranjo}
          {tom}
        </div>
      );

    case "announcement":
      return (
        <div className={styles.fields}>
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Aviso
            <input name="text" maxLength={140} required defaultValue={str(block.text)} />
            <span className={styles.fieldHint}>Uma linha. Ex.: “Fechado dia 7” ou “Frete grátis acima de R$ 150”.</span>
          </label>
          <label className={styles.field}>
            Leva para
            <select name="link_target" defaultValue={str(block.link_target) || "none"}>
              <option value="none">lugar nenhum</option>
              <option value="catalog">os produtos</option>
              <option value="chat">o atendimento</option>
            </select>
          </label>
          {tom}
        </div>
      );

    case "featured_products":
      return (
        <div className={styles.fields}>
          <label className={styles.field}>
            Título
            <input name="title" maxLength={80} required defaultValue={str(block.title)} />
          </label>
          {arranjo}
          <fieldset className={`${styles.fieldWide} ${local.group}`}>
            <legend>Produtos (até 12)</legend>
            <div className={styles.flags}>
              {products.map((product) => (
                <label key={product.id} className={styles.check}>
                  <input
                    type="checkbox"
                    name="product_ids"
                    value={product.id}
                    defaultChecked={(block.product_ids as string[] | undefined)?.includes(product.id)}
                  />
                  {product.name}
                </label>
              ))}
            </div>
          </fieldset>
        </div>
      );

    case "categories":
      return (
        <div className={styles.fields}>
          <label className={styles.field}>
            Título
            <input name="title" maxLength={80} required defaultValue={str(block.title)} />
          </label>
          {arranjo}
          <fieldset className={`${styles.fieldWide} ${local.group}`}>
            <legend>Categorias (até 12)</legend>
            <div className={styles.flags}>
              {categories.map((category) => (
                <label key={category.id} className={styles.check}>
                  <input
                    type="checkbox"
                    name="category_ids"
                    value={category.id}
                    defaultChecked={(block.category_ids as string[] | undefined)?.includes(category.id)}
                  />
                  {category.name}
                </label>
              ))}
            </div>
          </fieldset>
        </div>
      );

    case "benefits": {
      const items = list(block.items);
      const linhas = items.length ? items : [{}, {}];
      return (
        <div className={styles.fields}>
          <label className={styles.field}>
            Título
            <input name="title" maxLength={80} defaultValue={str(block.title)} />
          </label>
          {arranjo}
          {tom}
          <fieldset className={`${styles.fieldWide} ${local.group}`}>
            <legend>Motivos (de 2 a 6)</legend>
            {/* Seis linhas fixas: quem quer menos deixa em branco, e linha sem título sai fora
                no salvar. Acrescentar linha com botão exigiria JavaScript. */}
            {Array.from({ length: 6 }, (_, i) => {
              const item = linhas[i] ?? {};
              return (
                <div key={i} className={local.row3}>
                  <select name="benefit_icon" defaultValue={str(item.icon) || "store"}>
                    {ICONS.map((icon) => (
                      <option key={icon} value={icon}>
                        {icon}
                      </option>
                    ))}
                  </select>
                  <input name="benefit_title" maxLength={40} placeholder="Motivo" defaultValue={str(item.title)} />
                  <input name="benefit_text" maxLength={140} placeholder="Detalhe (opcional)" defaultValue={str(item.text)} />
                </div>
              );
            })}
          </fieldset>
        </div>
      );
    }

    case "text":
      return (
        <div className={styles.fields}>
          <label className={styles.field}>
            Título
            <input name="title" maxLength={80} defaultValue={str(block.title)} />
          </label>
          {arranjo}
          {tom}
          {imagem("media_id", block.media_id)}
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Texto
            <textarea name="body" rows={5} maxLength={2000} defaultValue={str(block.body)} />
          </label>
        </div>
      );

    case "gallery":
      return (
        <div className={styles.fields}>
          <label className={styles.field}>
            Título
            <input name="title" maxLength={80} defaultValue={str(block.title)} />
          </label>
          {arranjo}
          <fieldset className={`${styles.fieldWide} ${local.group}`}>
            <legend>Fotos (até 12)</legend>
            <div className={styles.flags}>
              {media
                .filter((m) => m.status === "ready")
                .map((m, i) => (
                  <label key={m.id} className={styles.check}>
                    <input
                      type="checkbox"
                      name="media_ids"
                      value={m.id}
                      defaultChecked={(block.media_ids as string[] | undefined)?.includes(m.id)}
                    />
                    Imagem {i + 1}
                    {m.alt ? ` — ${m.alt}` : ""}
                  </label>
                ))}
            </div>
          </fieldset>
        </div>
      );

    case "testimonials": {
      const items = list(block.items);
      return (
        <div className={styles.fields}>
          <label className={styles.field}>
            Título
            <input name="title" maxLength={80} defaultValue={str(block.title)} />
          </label>
          {arranjo}
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Depoimentos
            <textarea
              name="items"
              rows={5}
              defaultValue={items.map((i) => `${str(i.author)} = ${str(i.text)}`).join("\n")}
            />
            <span className={styles.fieldHint}>Um por linha, no formato “Nome = o que a pessoa disse”.</span>
          </label>
        </div>
      );
    }

    case "faq": {
      const items = list(block.items);
      return (
        <div className={styles.fields}>
          <label className={styles.field}>
            Título
            <input name="title" maxLength={80} defaultValue={str(block.title)} />
          </label>
          {arranjo}
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Perguntas
            <textarea
              name="items"
              rows={6}
              defaultValue={items.map((i) => `${str(i.question)} = ${str(i.answer)}`).join("\n")}
            />
            <span className={styles.fieldHint}>Uma por linha, no formato “Pergunta = resposta”.</span>
          </label>
        </div>
      );
    }

    case "hours": {
      const days = list(block.days);
      const byDay = new Map(days.map((d) => [Number(d.weekday), d]));
      return (
        <div className={styles.fields}>
          <label className={styles.field}>
            Título
            <input name="title" maxLength={80} defaultValue={str(block.title)} />
          </label>
          {arranjo}
          <label className={styles.field}>
            Endereço
            <input name="address" maxLength={300} defaultValue={str(block.address)} />
          </label>
          <label className={styles.field}>
            Cidade
            <input name="city" maxLength={80} defaultValue={str(block.city)} />
          </label>
          <label className={styles.field}>
            UF
            <input name="state" maxLength={2} defaultValue={str(block.state)} placeholder="SP" />
          </label>
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Observação
            <input name="note" maxLength={140} defaultValue={str(block.note)} />
          </label>
          <fieldset className={`${styles.fieldWide} ${local.group}`}>
            <legend>Horários</legend>
            {/* Dia sem horário preenchido não entra: é como se diz "fechado" sem um campo a mais. */}
            {DIAS.map((nome, i) => {
              const dia = byDay.get(i);
              return (
                <div key={i} className={local.row3}>
                  <input type="hidden" name="day_weekday" value={i} />
                  <span className={local.dayName}>{nome}</span>
                  <input name="day_opens" type="time" defaultValue={str(dia?.opens)} aria-label={`${nome}, abre`} />
                  <input name="day_closes" type="time" defaultValue={str(dia?.closes)} aria-label={`${nome}, fecha`} />
                </div>
              );
            })}
          </fieldset>
        </div>
      );
    }

    case "cta":
      return (
        <div className={styles.fields}>
          <label className={styles.field}>
            Título
            <input name="title" maxLength={80} required defaultValue={str(block.title)} />
          </label>
          <label className={styles.field}>
            Subtítulo
            <input name="subtitle" maxLength={200} defaultValue={str(block.subtitle)} />
          </label>
          <label className={styles.field}>
            Texto do botão
            <input name="cta_label" maxLength={30} required defaultValue={str(block.cta_label)} />
          </label>
          <label className={styles.field}>
            O botão leva para
            <select name="cta_target" defaultValue={str(block.cta_target) || "catalog"}>
              <option value="catalog">os produtos</option>
              <option value="chat">o atendimento</option>
            </select>
          </label>
          {tom}
        </div>
      );

    case "contact":
      return (
        <div className={styles.fields}>
          <label className={styles.field}>
            Título
            <input name="title" maxLength={80} defaultValue={str(block.title) || "Contato"} />
          </label>
          {arranjo}
          <label className={styles.field}>
            WhatsApp
            <input name="whatsapp_e164" inputMode="tel" defaultValue={str(block.whatsapp_e164)} placeholder="11 99999-9999" />
          </label>
          <label className={styles.field}>
            Instagram
            <input name="instagram" maxLength={30} defaultValue={str(block.instagram)} placeholder="sualoja" />
          </label>
          <label className={styles.field}>
            E-mail
            <input name="email" type="email" maxLength={254} defaultValue={str(block.email)} />
          </label>
          <label className={styles.field}>
            Endereço
            <input name="address" maxLength={300} defaultValue={str(block.address)} />
          </label>
          <label className={styles.field}>
            Horário
            <input name="hours" maxLength={300} defaultValue={str(block.hours)} />
          </label>
        </div>
      );

    default:
      return null;
  }
}
