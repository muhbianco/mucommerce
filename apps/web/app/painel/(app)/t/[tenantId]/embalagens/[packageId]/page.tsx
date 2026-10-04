import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { ApiError, api, requireMe } from "@/lib/panel/api";
import { dimsLabel, weightLabel } from "@/lib/panel/measure";
import {
  PACKAGE_KIND_LABEL,
  PREVIEW_WARNING,
  type PackagePreview,
  type PackageUsage,
  type ShippingPackage,
} from "@/lib/panel/packaging";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../../../panel.module.css";
import { Flash } from "../../flash";
import { KeyValues, PageHeader, Pill, Section } from "../../ui";
import { deletePackage, makeDefault, setActive, updatePackage } from "../actions";
import local from "../embalagens.module.css";
import { BoxDrawing, PackageForm } from "../package-form";

export const metadata: Metadata = { title: "Embalagem" };

export default async function PackageDetail({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string; packageId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId, packageId } = await params;
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  const scopes = tenantScopes(me, context.tenant_id);
  const f = context.features;
  if (!f.checkout || !f["shipping.packing_v2"] || !scopes.can("catalog:read")) notFound();
  if (!/^[0-9a-f-]{36}$/.test(packageId)) notFound();
  const path = `/admin/tenants/${context.tenant_id}/shipping`;
  let pkg: ShippingPackage;
  try {
    pkg = await api<ShippingPackage>(`${path}/packages/${packageId}`);
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) notFound();
    throw error;
  }
  const [previa, usos] = await Promise.all([
    api<PackagePreview>(`${path}/packing-preview/package`, {
      json: {
        kind: pkg.kind,
        inner_length_mm: pkg.inner_length_mm,
        inner_width_mm: pkg.inner_width_mm,
        inner_height_mm: pkg.inner_height_mm,
        outer_length_mm: pkg.outer_length_mm,
        outer_width_mm: pkg.outer_width_mm,
        outer_height_mm: pkg.outer_height_mm,
        empty_weight_grams: pkg.empty_weight_grams,
        max_weight_grams: pkg.max_weight_grams,
      },
    }),
    api<PackageUsage[]>(`${path}/packages/${packageId}/products`),
  ]);
  const canWrite = scopes.can("catalog:write");
  const base = `/t/${context.tenant_id}`;
  const hidden = (
    <>
      <input type="hidden" name="tenant_id" value={context.tenant_id} />
      <input type="hidden" name="package_id" value={pkg.id} />
      <input type="hidden" name="back" value="detalhe" />
    </>
  );

  return (
    <>
      <PageHeader
        eyebrow="Embalagens"
        title={pkg.name}
        lead={`${PACKAGE_KIND_LABEL[pkg.kind]}${pkg.is_default ? " · embalagem padrão da loja" : ""}${
          pkg.active ? "" : " · arquivada"
        }`}
        actions={
          <Link href={`${base}/embalagens`} className={styles.buttonGhost}>
            Voltar
          </Link>
        }
      />
      <Flash ok={ok} erro={erro} />
      <div className={styles.split}>
        <div>
          <Section title="Dados da embalagem">
            {canWrite ? (
              <PackageForm tenantId={context.tenant_id} action={updatePackage} pkg={pkg} submitLabel="Salvar embalagem" />
            ) : (
              <KeyValues
                items={[
                  { label: "Por dentro", value: dimsLabel([pkg.inner_length_mm, pkg.inner_width_mm, pkg.inner_height_mm]) },
                  { label: "Por fora (cobrado)", value: dimsLabel(pkg.billed_outer_mm) },
                  { label: "Peso vazia", value: weightLabel(pkg.empty_weight_grams) },
                  { label: "Aguenta até", value: weightLabel(pkg.max_weight_grams) },
                ]}
              />
            )}
          </Section>

          {canWrite ? (
            <Section title="Situação" description={pkg.is_default ? "A padrão não arquiva nem apaga." : undefined}>
              {pkg.is_default ? (
                <p className={styles.hint}>
                  Para arquivar esta embalagem, torne outra embalagem padrão antes, na lista de Embalagens.
                </p>
              ) : null}
              <div className={local.rowForms}>
                {!pkg.is_default && pkg.active ? (
                  <form action={makeDefault}>
                    {hidden}
                    <button type="submit">Tornar padrão</button>
                  </form>
                ) : null}
                {!pkg.active ? (
                  <form action={setActive}>
                    {hidden}
                    <input type="hidden" name="active" value="1" />
                    <button type="submit">Reativar</button>
                  </form>
                ) : null}
                {!pkg.active && pkg.rules_count === 0 ? (
                  <form action={deletePackage}>
                    {hidden}
                    <button type="submit" className={styles.buttonDanger}>
                      Apagar de vez
                    </button>
                  </form>
                ) : null}
              </div>
              {!pkg.is_default && pkg.active ? (
                <details>
                  <summary>Arquivar esta embalagem</summary>
                  {usos.length ? (
                    <>
                      <p className={styles.note}>
                        {usos.length === 1 ? "1 produto usa" : `${usos.length} produtos usam`} esta embalagem. Se eles
                        só usavam ela, passam para a escolha automática:
                      </p>
                      <ul className={styles.steps}>
                        {usos.map((u) => (
                          <li key={u.product_id}>
                            <Link href={`${base}/produtos/${u.product_id}#envio`}>{u.name}</Link>
                          </li>
                        ))}
                      </ul>
                    </>
                  ) : (
                    <p className={styles.hint}>Nenhum produto escolheu esta embalagem: arquivar não muda nenhum produto.</p>
                  )}
                  <form action={setActive}>
                    {hidden}
                    <input type="hidden" name="active" value="0" />
                    <button type="submit" className={styles.buttonDanger}>
                      Arquivar
                    </button>
                  </form>
                </details>
              ) : null}
            </Section>
          ) : null}
        </div>

        <aside>
          <Section title="Prévia">
            <BoxDrawing dims={previa.billed_outer_mm} />
            <KeyValues
              items={[
                { label: "Para a transportadora", value: `${dimsLabel(previa.billed_outer_mm)} por fora` },
                {
                  label: "Peso cúbico",
                  value: previa.cubic_free
                    ? `${weightLabel(previa.cubic_grams)} — nos Correios conta o peso real (até 5 kg de cubagem)`
                    : weightLabel(previa.cubic_grams),
                },
              ]}
            />
            {previa.warnings.map((w) => (
              <p key={w} className={styles.note} role="status">
                {PREVIEW_WARNING[w] ?? w}
              </p>
            ))}
            {previa.fits.length ? (
              <>
                <h4 className={local.subhead}>Cabem</h4>
                <ul className={local.fits}>
                  {previa.fits.map((fit) => (
                    <li key={fit.product_id}>
                      <span>{fit.name}</span>
                      {fit.units > 0 ? <strong>{fit.units} un.</strong> : <Pill state="off">não cabe</Pill>}
                    </li>
                  ))}
                </ul>
                <p className={styles.fieldHint}>Pelos seus últimos produtos com medida, cada um sozinho na embalagem.</p>
              </>
            ) : (
              <p className={styles.hint}>Cadastre peso e medidas nos produtos para ver quantos cabem aqui.</p>
            )}
          </Section>
        </aside>
      </div>
    </>
  );
}
