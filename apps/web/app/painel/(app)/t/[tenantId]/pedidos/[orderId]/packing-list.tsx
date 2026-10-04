import { dimsLabel, weightLabel } from "@/lib/panel/measure";
import { type FrozenPlan, LABEL_MODE_TEXT, PACKAGE_KIND_LABEL, type PackageKind } from "@/lib/panel/packaging";

import styles from "../../../../../panel.module.css";
import { Pill, Section } from "../../ui";
import local from "./order.module.css";
import { PrintButton } from "./print-button";

/**
 * "Como embalar": os volumes com que o pedido foi cotado e pago. A etiqueta sai com estas
 * medidas e pesos — embalar diferente é o que gera cobrança de divergência da transportadora.
 */
export function PackingList({ plan, orderNumber }: { plan: FrozenPlan; orderNumber: number }) {
  const total = plan.parcels.length;
  return (
    <div className={local.packing}>
      <Section
        id="como-embalar"
        title="Como embalar"
        description={`${total} ${total === 1 ? "volume" : "volumes"} · ${LABEL_MODE_TEXT[plan.label_mode] ?? plan.label_mode}`}
        actions={
          <PrintButton className={`${styles.buttonGhost} ${styles.buttonSmall}`}>Imprimir lista</PrintButton>
        }
      >
        <p className={local.printTitle}>Pedido #{orderNumber} — lista de embalagem</p>
        <ol className={local.parcels}>
          {plan.parcels.map((volume) => (
            <li key={volume.n} className={local.parcel}>
              <div className={local.parcelHead}>
                <strong>
                  Volume {volume.n} de {total} — {embalagem(volume.package_name, volume.kind, volume.own)}
                </strong>
                <span className={local.parcelMeta}>
                  {[dimsLabel(volume.inner_mm ?? volume.outer_mm), weightLabel(volume.weight_grams)]
                    .filter(Boolean)
                    .join(" · ")}
                </span>
                <span className={local.parcelBadges}>
                  {volume.own ? <Pill state="info">vai na embalagem dele</Pill> : null}
                  {volume.oversize ? <Pill state="warn">maior que suas embalagens</Pill> : null}
                  {volume.declared ? <Pill state="info">capacidade declarada pela loja</Pill> : null}
                </span>
              </div>
              <ul className={local.parcelItems}>
                {volume.items.map((item) => (
                  <li key={`${item.sku}-${item.name}`}>
                    <span className={local.check} aria-hidden="true" />
                    {item.units}× {item.name}
                    {item.sku ? <span className={local.sku}> · {item.sku}</span> : null}
                  </li>
                ))}
              </ul>
              {dica(volume) ? <p className={styles.hint}>{dica(volume)}</p> : null}
            </li>
          ))}
        </ol>
        {plan.degraded ? (
          <p className={styles.hint}>
            Pedido grande: a combinação foi montada produto a produto, sem misturar. Pode sobrar espaço.
          </p>
        ) : null}
      </Section>
    </div>
  );
}

function embalagem(name: string, kind: string, own: boolean): string {
  if (own) return name || "embalagem do produto";
  return name || PACKAGE_KIND_LABEL[kind as PackageKind] || "Embalagem";
}

function dica(volume: FrozenPlan["parcels"][number]): string {
  if (volume.own) return "Cole a etiqueta direto na embalagem do produto.";
  if (volume.oversize) return "Não coube em nenhuma embalagem sua: vai sozinho, com a medida dele.";
  if (volume.declared)
    return "Cabe porque você declarou que o produto acomoda nesta embalagem. Confira ao fechar.";
  return "";
}
