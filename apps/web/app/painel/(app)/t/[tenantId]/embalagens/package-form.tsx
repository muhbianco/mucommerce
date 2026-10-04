/**
 * Formulário de uma embalagem (nova, edição e o assistente da primeira vez).
 *
 * Funciona sem JavaScript: o tipo escolhido troca os rótulos por CSS (`:has`), e o servidor
 * ignora o campo que não vale para o tipo (diâmetro fora do tubo, largura e altura no tubo).
 * Sem `:has` no navegador, todos os campos aparecem — feio, mas certo.
 */
import { cmInput } from "@/lib/panel/measure";
import { moneyInput } from "@/lib/panel/format";
import { PACKAGE_KIND_LABEL, type PackageKind, type ShippingPackage } from "@/lib/panel/packaging";

import styles from "../../../../panel.module.css";
import local from "./embalagens.module.css";

const KINDS: { kind: PackageKind; hint: string }[] = [
  { kind: "box", hint: "Papelão, a mais comum" },
  { kind: "envelope", hint: "Plástico ou papel, para peças finas" },
  { kind: "tube", hint: "Pôster, mapa, tecido enrolado" },
  { kind: "bag", hint: "Saco plástico de envio" },
];

export function PackageForm({
  tenantId,
  action,
  pkg,
  submitLabel,
  first = false,
  defaults,
}: {
  tenantId: string;
  action: (form: FormData) => Promise<void>;
  pkg?: ShippingPackage;
  submitLabel: string;
  first?: boolean;
  defaults?: { kind: PackageKind; inner: [number, number, number]; tare: number; name: string };
}) {
  const kind = pkg?.kind ?? defaults?.kind ?? "box";
  const inner = pkg ? [pkg.inner_length_mm, pkg.inner_width_mm, pkg.inner_height_mm] : (defaults?.inner ?? []);
  const outerSet = pkg?.outer_length_mm != null;
  return (
    <form action={action} className={local.form}>
      <input type="hidden" name="tenant_id" value={tenantId} />
      {pkg ? <input type="hidden" name="package_id" value={pkg.id} /> : null}
      {first ? <input type="hidden" name="first" value="1" /> : null}

      <fieldset className={local.kinds}>
        <legend>Tipo</legend>
        {KINDS.map((item) => (
          <label key={item.kind} className={local.kindCard}>
            <input type="radio" name="kind" value={item.kind} defaultChecked={item.kind === kind} required />
            <span className={local.kindName}>{PACKAGE_KIND_LABEL[item.kind]}</span>
            <span className={local.kindHint}>{item.hint}</span>
          </label>
        ))}
      </fieldset>

      <div className={styles.fields}>
        <label className={`${styles.field} ${styles.fieldWide}`}>
          Nome
          <input name="name" maxLength={60} defaultValue={pkg?.name ?? defaults?.name ?? ""} placeholder="Caixa 30x20x15" />
          <span className={styles.fieldHint}>Vazio, o nome sai das medidas. É o que aparece no pedido, em “Como embalar”.</span>
        </label>
      </div>

      <h4 className={local.subhead}>Medidas por dentro (cm)</h4>
      <p className={styles.fieldHint}>O espaço onde os produtos vão. Use vírgula para meio centímetro: 30,5.</p>
      <div className={styles.fields}>
        <label className={styles.field}>
          Comprimento
          <input name="inner_length" inputMode="decimal" required defaultValue={cmInput(inner[0])} placeholder="30" />
        </label>
        <label className={`${styles.field} ${local.notTube}`}>
          Largura
          <input name="inner_width" inputMode="decimal" defaultValue={cmInput(inner[1])} placeholder="20" />
        </label>
        <label className={`${styles.field} ${local.notTube}`}>
          <span className={local.heightBox}>Altura</span>
          <span className={local.heightFlat}>Espessura máxima</span>
          <input name="inner_height" inputMode="decimal" defaultValue={cmInput(inner[2])} placeholder="15" />
        </label>
        <label className={`${styles.field} ${local.onlyTube}`}>
          Diâmetro
          <input name="inner_diameter" inputMode="decimal" defaultValue={kind === "tube" ? cmInput(inner[1]) : ""} placeholder="10" />
        </label>
      </div>

      <details className={local.outer} open={outerSet}>
        <summary>Medidas por fora (opcional)</summary>
        <p className={styles.fieldHint}>
          É o que a transportadora mede e cobra. Vazio, calculamos somando a parede (cerca de 0,4 cm por lado na
          caixa). Se você mediu por fora, informe as três.
        </p>
        <div className={styles.fields}>
          <label className={styles.field}>
            Comprimento por fora
            <input name="outer_length" inputMode="decimal" defaultValue={cmInput(pkg?.outer_length_mm)} />
          </label>
          <label className={styles.field}>
            Largura por fora
            <input name="outer_width" inputMode="decimal" defaultValue={cmInput(pkg?.outer_width_mm)} />
          </label>
          <label className={styles.field}>
            Altura por fora
            <input name="outer_height" inputMode="decimal" defaultValue={cmInput(pkg?.outer_height_mm)} />
          </label>
        </div>
      </details>

      <div className={styles.fields}>
        <label className={styles.field}>
          Peso vazia (g)
          <input
            name="empty_weight"
            inputMode="numeric"
            defaultValue={pkg?.empty_weight_grams ?? defaults?.tare ?? ""}
            placeholder="180"
          />
          <span className={styles.fieldHint}>Pese com o enchimento que você costuma usar (papel, plástico-bolha).</span>
        </label>
        <label className={styles.field}>
          Aguenta até (kg)
          <input
            name="max_weight"
            inputMode="decimal"
            defaultValue={pkg ? String(pkg.max_weight_grams / 1000).replace(".", ",") : "30"}
          />
          <span className={styles.fieldHint}>Os Correios levam até 30 kg por volume.</span>
        </label>
        <label className={styles.field}>
          Custo da embalagem (R$)
          <input
            name="material_cost"
            inputMode="decimal"
            defaultValue={pkg?.material_cost_cents ? moneyInput(pkg.material_cost_cents) : ""}
            placeholder="2,50"
          />
          <span className={styles.fieldHint}>Opcional. Desempata combinações e, se você ligar nas regras, entra no frete.</span>
        </label>
      </div>

      {first ? null : (
        <fieldset className={local.usage}>
          <legend>Uso</legend>
          <label className={styles.check}>
            <input type="radio" name="uso" value="auto" defaultChecked={pkg ? pkg.auto_select : true} />
            <span>
              Pode ser usada para qualquer produto <strong>(recomendado)</strong>
              <span className={styles.fieldHint}>O sistema escolhe entre as suas embalagens a combinação mais barata.</span>
            </span>
          </label>
          <label className={styles.check}>
            <input type="radio" name="uso" value="restricted" defaultChecked={pkg ? !pkg.auto_select : false} />
            <span>
              Só para os produtos que eu escolher
              <span className={styles.fieldHint}>
                Ex.: o tubo dos pôsteres. Escolha no produto, em “Envio e embalagem”.
                {pkg?.rules_count ? ` Hoje ${pkg.rules_count} produto(s) usam esta embalagem.` : ""}
              </span>
            </span>
          </label>
        </fieldset>
      )}

      {pkg && !pkg.is_default ? (
        <label className={styles.check}>
          <input type="checkbox" name="active" defaultChecked={pkg.active} />
          <span>Ativa</span>
        </label>
      ) : pkg ? (
        <input type="hidden" name="active" value="on" />
      ) : null}

      <div className={styles.formActions}>
        <button type="submit" className={styles.button}>
          {submitLabel}
        </button>
      </div>
    </form>
  );
}

/** Desenho em escala da embalagem (isométrico simples), só para dar noção de tamanho. */
export function BoxDrawing({ dims }: { dims: number[] }) {
  const [l = 1, w = 1, h = 1] = dims;
  const maior = Math.max(l, w, h, 1);
  const escala = 70 / maior;
  const L = l * escala;
  const W = w * escala;
  const H = h * escala;
  const cos = 0.866;
  const sin = 0.5;
  // Topo: frente-esquerda (0,0), direita (L), fundo (W); projeção isométrica.
  const p = (x: number, y: number, z: number) => [100 + (x - y) * cos, 110 - z + (x + y) * sin * -1 + 40] as const;
  const pts = (list: (readonly [number, number])[]) => list.map(([x, y]) => `${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const a = p(0, 0, 0);
  const b = p(L, 0, 0);
  const c = p(L, W, 0);
  const d = p(0, W, 0);
  const a2 = p(0, 0, H);
  const b2 = p(L, 0, H);
  const c2 = p(L, W, H);
  const d2 = p(0, W, H);
  return (
    <svg className={local.drawing} viewBox="0 0 200 170" role="img" aria-label="Desenho da embalagem em escala">
      <polygon points={pts([a, b, b2, a2])} className={local.faceFront} />
      <polygon points={pts([a, d, d2, a2])} className={local.faceSide} />
      <polygon points={pts([a2, b2, c2, d2])} className={local.faceTop} />
      <polyline points={pts([b, c, c2])} className={local.edgeHidden} />
    </svg>
  );
}
