/**
 * Produto → "Envio e embalagem" (frete v2, docs/13-frete-v2.md §7.5).
 *
 * Fica **dentro** do formulário grande do produto (um PATCH só). Funciona sem JavaScript: os
 * blocos que dependem do modo e de "é flexível" aparecem e somem por CSS (`:has`); sem `:has`,
 * tudo aparece e o servidor só lê o que vale para o modo escolhido. Nada de conta de encaixe
 * aqui — "Onde cabe" vem do servidor (o mesmo motor da cotação).
 */
import Link from "next/link";

import { cmInput, MEASURE_WARNING_TEXT, measureWarnings, weightInput } from "@/lib/panel/measure";
import {
  CAPACITY_REASON_TEXT,
  DECLARED_CHECK_TEXT,
  type PackageCapacity,
  type ShippingPackage,
} from "@/lib/panel/packaging";
import type { Product } from "@/lib/panel/types";

import styles from "../../../../../panel.module.css";
import pk from "./packing.module.css";

const PHYSICAL = new Set(["physical", "made_to_order"]);

export function PackingSection({
  product,
  packages,
  capacities,
  base,
  disabled,
}: {
  product: Product;
  packages: ShippingPackage[];
  capacities: PackageCapacity[] | null;
  base: string;
  disabled: boolean;
}) {
  if (!PHYSICAL.has(product.kind)) {
    return (
      <>
        <h4 id="envio" className={pk.subhead}>
          Envio e embalagem
        </h4>
        <p className={styles.hint}>Este produto não é enviado (serviço, digital ou ingresso): não entra na caixa.</p>
      </>
    );
  }
  const peso = weightInput(product.weight_grams);
  const modo = product.packing_mode ?? "auto";
  const regras = new Map((product.package_rules ?? []).map((r) => [r.package_id, r.max_units]));
  const ativas = packages.filter((p) => p.active);
  const avisos = measureWarnings(product.weight_grams, [product.width_mm, product.height_mm, product.depth_mm]);
  const vira = (product.packing_rotation ?? "any") === "any";
  const resumo = [
    vira ? "pode virar" : "este lado para cima",
    product.packing_flexible ? "flexível" : "não é flexível",
    product.packing_ship_alone ? "vai sozinho" : "pode ir com outros",
  ].join(" · ");
  const medido = Boolean(product.weight_grams && product.width_mm && product.height_mm && product.depth_mm);

  return (
    <div className={pk.packing}>
      <h4 id="envio" className={pk.subhead}>
        Envio e embalagem
      </h4>
      <input type="hidden" name="packing_v2" value="1" />
      <div className={styles.fields}>
        <label className={styles.field}>
          Peso
          <span className={pk.weightRow}>
            <input name="weight_value" inputMode="decimal" defaultValue={peso.value} disabled={disabled} placeholder="150" />
            <select name="weight_unit" defaultValue={peso.unit} disabled={disabled} aria-label="Unidade do peso">
              <option value="g">g</option>
              <option value="kg">kg</option>
            </select>
          </span>
        </label>
        <label className={styles.field}>
          Comprimento (cm)
          <input name="depth_cm" inputMode="decimal" defaultValue={cmInput(product.depth_mm)} disabled={disabled} placeholder="10" />
        </label>
        <label className={styles.field}>
          Largura (cm)
          <input name="width_cm" inputMode="decimal" defaultValue={cmInput(product.width_mm)} disabled={disabled} placeholder="10" />
        </label>
        <label className={styles.field}>
          Altura (cm)
          <input name="height_cm" inputMode="decimal" defaultValue={cmInput(product.height_mm)} disabled={disabled} placeholder="5" />
        </label>
      </div>
      <p className={styles.fieldHint}>
        Meça o produto como ele entra na caixa de envio: com a embalagem dele (saquinho, caixinha), sem a caixa da loja.
        Sem peso e as três medidas, o carrinho não mostra frete para ele.
      </p>
      {avisos.map((aviso) => (
        <p key={aviso} className={styles.note} role="status">
          {MEASURE_WARNING_TEXT[aviso]}
        </p>
      ))}

      <fieldset className={pk.modes}>
        <legend>Como ele é embalado</legend>
        <label className={pk.mode}>
          <input type="radio" name="packing_mode" value="auto" defaultChecked={modo === "auto"} disabled={disabled} />
          <span>
            <span className={pk.modeTitle}>
              <strong>Automático</strong> (recomendado)
            </span>
            <span className={styles.fieldHint}>O sistema escolhe a combinação mais barata entre as suas embalagens.</span>
          </span>
        </label>
        <label className={pk.mode}>
          <input
            type="radio"
            name="packing_mode"
            value="restricted"
            defaultChecked={modo === "restricted"}
            disabled={disabled}
          />
          <span>
            <strong>Só em embalagens específicas</strong>
            <span className={styles.fieldHint}>Ex.: pôster só no tubo, vidro só na caixa com divisória.</span>
          </span>
        </label>
        <div className={pk.rules}>
          {ativas.length ? (
            ativas.map((pkg) => (
              <div key={pkg.id} className={pk.rule}>
                <label className={styles.check}>
                  <input
                    type="checkbox"
                    name="rule_pkg"
                    value={pkg.id}
                    defaultChecked={regras.has(pkg.id)}
                    disabled={disabled}
                  />
                  <span>{pkg.name}</span>
                </label>
                <label className={pk.ruleMax}>
                  máximo de
                  <input
                    name={`rule_max_${pkg.id}`}
                    type="number"
                    min={1}
                    max={100000}
                    defaultValue={regras.get(pkg.id) ?? ""}
                    disabled={disabled}
                    aria-label={`Máximo de unidades em ${pkg.name}`}
                  />
                  unidades (opcional)
                </label>
              </div>
            ))
          ) : (
            <p className={styles.hint}>
              Nenhuma embalagem ativa. <Link href={`${base}/embalagens`}>Cadastre em Embalagens</Link>.
            </p>
          )}
          <p className={`${styles.fieldHint} ${pk.hintRigid}`}>
            O “máximo de unidades” só limita: o sistema nunca coloca mais do que cabe de verdade.
          </p>
          <p className={`${styles.fieldHint} ${pk.hintFlexible}`}>
            Produto flexível: o “máximo de unidades” é uma declaração sua e o sistema confia nele — até o dobro do espaço
            da caixa (mais do que isso, o produto teria de encolher para menos da metade).
          </p>
        </div>
        <label className={pk.mode}>
          <input
            type="radio"
            name="packing_mode"
            value="own_container"
            defaultChecked={modo === "own_container"}
            disabled={disabled}
          />
          <span>
            <strong>Já vai pronto na embalagem dele</strong>
            <span className={styles.fieldHint}>
              Caixa do fabricante, tubo próprio: cada unidade é um volume, com a medida acima.
            </span>
          </span>
        </label>
      </fieldset>

      <details className={pk.traits}>
        <summary>Características · {resumo}</summary>
        <label className={styles.check}>
          <input type="checkbox" name="packing_turns" defaultChecked={vira} disabled={disabled} />
          <span>
            Pode ser virado ou deitado
            <span className={styles.fieldHint}>Desmarcado = “este lado para cima”: a altura fica sempre de pé.</span>
          </span>
        </label>
        <label className={styles.check}>
          <input
            type="checkbox"
            name="packing_flexible"
            defaultChecked={product.packing_flexible ?? false}
            disabled={disabled}
          />
          <span>
            É flexível: dobra ou amassa sem estragar
            <span className={styles.fieldHint}>Roupa, tecido, rabiola. Ocupa o espaço que sobra entre as peças duras.</span>
          </span>
        </label>
        <label className={styles.check}>
          <input
            type="checkbox"
            name="packing_ship_alone"
            defaultChecked={product.packing_ship_alone ?? false}
            disabled={disabled}
          />
          <span>
            Enviar sempre separado dos outros produtos
            <span className={styles.fieldHint}>Variações do mesmo produto ainda podem ir juntas.</span>
          </span>
        </label>
      </details>

      <div className={pk.fit}>
        <h5>Onde cabe</h5>
        {!medido ? (
          <p className={styles.hint}>Informe peso e as três medidas e salve para ver onde ele cabe.</p>
        ) : modo === "own_container" ? (
          <p className={styles.hint}>Vai na embalagem dele: cada unidade é um volume, sem caixa da loja.</p>
        ) : capacities && capacities.length ? (
          <ul className={pk.fitList}>
            {capacities.map((c) => (
              <li key={c.package_id} data-allowed={c.allowed ? "true" : undefined}>
                <span>
                  {c.name}
                  {!c.allowed ? <span className={styles.fieldHint}> (fora da escolha deste produto)</span> : null}
                </span>
                <span>
                  {c.calculated === 0 ? (
                    <em>{CAPACITY_REASON_TEXT[c.reason ?? ""] ?? "não cabe"}</em>
                  ) : c.declared !== null && product.packing_flexible ? (
                    <>
                      <strong>{c.calculated}</strong> calculadas · <strong>{c.effective}</strong> declaradas{" "}
                      <span className={pk.declaredBadge}>declarada</span>
                    </>
                  ) : c.declared !== null ? (
                    // Rígido: a declaração só limita; nunca passa do que cabe de verdade.
                    <>
                      até <strong>{c.effective}</strong> {c.effective === 1 ? "unidade" : "unidades"} · limite seu:{" "}
                      {c.declared}
                    </>
                  ) : (
                    <>
                      até <strong>{c.effective}</strong> {c.effective === 1 ? "unidade" : "unidades"}
                    </>
                  )}
                </span>
                {c.declared_check && DECLARED_CHECK_TEXT[c.declared_check] ? (
                  <span className={styles.fieldHint}>{DECLARED_CHECK_TEXT[c.declared_check]}</span>
                ) : null}
              </li>
            ))}
          </ul>
        ) : (
          <p className={styles.hint}>
            Nenhuma embalagem cadastrada. <Link href={`${base}/embalagens`}>Cadastre em Embalagens</Link>.
          </p>
        )}
        {medido ? (
          <Link href={`${base}/embalagens?p0=${product.id}&q0=1#simulador`} className={styles.fieldHint}>
            Testar frete deste produto →
          </Link>
        ) : null}
      </div>
    </div>
  );
}
