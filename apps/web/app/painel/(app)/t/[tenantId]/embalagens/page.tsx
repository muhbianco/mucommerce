import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { ApiError, api, requireMe } from "@/lib/panel/api";
import { formatMoney } from "@/lib/panel/format";
import { cmInput, dimsLabel, weightLabel } from "@/lib/panel/measure";
import {
  DEFAULT_PRESET,
  PACKAGE_KIND_LABEL,
  PACKAGE_PRESETS,
  QUOTE_PROBLEM_TEXT,
  STRATEGY_LABEL,
  packingSettings,
  type ShippingPackage,
  type SimulateOut,
} from "@/lib/panel/packaging";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type { Page, Product, ProductSummary } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { EmptyState, KeyValues, PageHeader, Pill, Section } from "../ui";
import { addPreset, createPackage, deletePackage, makeDefault, savePackingRules, setActive } from "./actions";
import local from "./embalagens.module.css";
import { PackageForm } from "./package-form";

export const metadata: Metadata = { title: "Embalagens" };

const SIM_ROWS = 4;
const PHYSICAL = new Set(["physical", "made_to_order"]);

function innerDims(pkg: ShippingPackage): number[] {
  return [pkg.inner_length_mm, pkg.inner_width_mm, pkg.inner_height_mm];
}

/** "30 × 20 × 15 cm por dentro · até 30 kg · R$ 2,50" */
function summary(pkg: ShippingPackage): string {
  const partes = [
    pkg.kind === "tube"
      ? `Ø ${cmInput(pkg.inner_width_mm)} × ${cmInput(pkg.inner_length_mm)} cm por dentro`
      : `${dimsLabel(innerDims(pkg))} por dentro`,
    `até ${weightLabel(pkg.max_weight_grams)}`,
  ];
  if (pkg.material_cost_cents) partes.push(formatMoney(pkg.material_cost_cents));
  return partes.join(" · ");
}

/** Linhas do simulador vindas do formulário GET (`p0`/`q0`…): ids conferidos pelo formato. */
function parseSim(query: Record<string, string | undefined>): { productId: string; units: number }[] {
  const linhas: { productId: string; units: number }[] = [];
  for (let i = 0; i < SIM_ROWS; i += 1) {
    const productId = query[`p${i}`] ?? "";
    const units = Math.floor(Number(query[`q${i}`] ?? "1"));
    if (/^[0-9a-f-]{36}$/.test(productId) && units >= 1 && units <= 500) linhas.push({ productId, units });
  }
  return linhas;
}

export default async function Packages({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<Record<string, string | undefined>>;
}) {
  const { tenantId } = await params;
  const query = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  const scopes = tenantScopes(me, context.tenant_id);
  const f = context.features;
  if (!f.checkout || !scopes.can("catalog:read")) notFound();
  const canWrite = scopes.can("catalog:write");
  const canRules = scopes.can("settings:write");
  const base = `/t/${context.tenant_id}`;
  const path = `/admin/tenants/${context.tenant_id}`;

  const [packages, products] = await Promise.all([
    api<ShippingPackage[]>(`${path}/shipping/packages`),
    api<Page<ProductSummary>>(`${path}/products?limit=100`).catch(() => ({ items: [], next_cursor: null })),
  ]);
  const regras = packingSettings(context.settings);
  const padrao = packages.find((p) => p.is_default) ?? null;
  const outras = packages.filter((p) => !p.is_default && p.active);
  const arquivadas = packages.filter((p) => !p.active);
  const vendaveis = products.items.filter((p) => PHYSICAL.has(p.kind) && p.status !== "archived");
  const shared = <input type="hidden" name="tenant_id" value={context.tenant_id} />;

  // Simulador: monta as caixas de um carrinho de teste (o preço real entra com a cotação nova).
  const pedidos = parseSim(query);
  let simulacao: SimulateOut | null = null;
  let simErro: string | null = null;
  const nomes = new Map<string, string>();
  if (pedidos.length) {
    try {
      const detalhes = await Promise.all(pedidos.map((p) => api<Product>(`${path}/products/${p.productId}`)));
      const lines = detalhes.flatMap((produto, i) => {
        const variante = produto.variants.find((v) => v.status === "active") ?? produto.variants[0];
        if (!variante) return [];
        nomes.set(variante.id, produto.name);
        return [{ variant_id: variante.id, quantity_milli: (pedidos[i]?.units ?? 1) * 1000 }];
      });
      const cep = (query.cep ?? "").replace(/\D/g, "");
      simulacao = await api<SimulateOut>(`${path}/shipping/simulate`, {
        json: { lines, ...(cep.length === 8 ? { postal_code: cep } : {}) },
      });
    } catch (error) {
      if (!(error instanceof ApiError)) throw error;
      simErro = error.code;
    }
  }

  const presetEscolhido = PACKAGE_PRESETS.find((p) => p.key === query.tamanho) ?? DEFAULT_PRESET;

  const row = (pkg: ShippingPackage, actions: ReactNode): ReactNode => (
    <div key={pkg.id} className={styles.row}>
      <div className={styles.rowMain}>
        <Link href={`${base}/embalagens/${pkg.id}`}>
          <strong>{pkg.name}</strong>
        </Link>
        <p className={`${styles.rowSub} ${local.dims}`}>
          {PACKAGE_KIND_LABEL[pkg.kind]} · {summary(pkg)}
        </p>
        <p className={styles.rowSub}>Para a transportadora: {dimsLabel(pkg.billed_outer_mm)} por fora</p>
      </div>
      <div className={styles.rowBadges}>
        {pkg.is_default ? <Pill state="live">Padrão</Pill> : null}
        {pkg.auto_select ? (
          <Pill state="info">Automática</Pill>
        ) : (
          <Pill state="pending">
            Só produtos escolhidos{pkg.rules_count ? ` (${pkg.rules_count})` : ""}
          </Pill>
        )}
      </div>
      {canWrite ? <div className={local.rowForms}>{actions}</div> : null}
    </div>
  );

  return (
    <>
      <PageHeader
        eyebrow="Envio"
        title="Embalagens"
        lead="Opcional. Sem embalagem cadastrada, cada pedido sai numa caixa sob medida — a menor que leva o que foi vendido. Se você usa caixas de tamanho fixo, cadastre aqui: o sistema monta a combinação que deixa o frete mais barato."
        actions={
          canWrite && packages.length ? (
            <Link href={`${base}/embalagens/nova`} className={styles.button}>
              Nova embalagem
            </Link>
          ) : undefined
        }
      />
      <Flash ok={query.ok} erro={query.erro} />

      {!packages.length ? (
        <Section
          title="Sua embalagem padrão"
          description="Opcional: sem ela, o frete sai em caixa sob medida. Cadastre se você tem uma caixa que usa sempre."
        >
          <p className={styles.hint}>Comece por um tamanho comum e ajuste as medidas para as da sua caixa:</p>
          <nav className={local.presets} aria-label="Tamanhos para começar">
            {PACKAGE_PRESETS.map((preset) => (
              <Link
                key={preset.key}
                href={`${base}/embalagens?tamanho=${preset.key}`}
                className={`${preset.key === presetEscolhido.key ? styles.button : styles.buttonGhost} ${styles.buttonSmall}`}
                aria-current={preset.key === presetEscolhido.key ? "true" : undefined}
              >
                {preset.label} · {dimsLabel(preset.inner)}
              </Link>
            ))}
          </nav>
          <p className={styles.fieldHint}>
            São pontos de partida, não medidas oficiais dos Correios: meça a sua caixa por dentro.
          </p>
          {canWrite ? (
            <PackageForm
              key={presetEscolhido.key}
              tenantId={context.tenant_id}
              action={createPackage}
              submitLabel="Salvar embalagem padrão"
              first
              defaults={{
                kind: presetEscolhido.kind,
                inner: presetEscolhido.inner,
                tare: presetEscolhido.tare,
                name: presetEscolhido.label,
              }}
            />
          ) : (
            <p className={styles.note}>Peça a quem cuida do catálogo para cadastrar a embalagem padrão.</p>
          )}
        </Section>
      ) : (
        <>
          {padrao ? (
            <Section
              title="Embalagem padrão"
              description={
                outras.length
                  ? "A que você mais usa. Para arquivar, torne outra padrão antes."
                  : "É a única. Sem ela, os pedidos saem em caixa sob medida."
              }
            >
              <div className={styles.rows}>
                {row(
                  padrao,
                  <>
                    <Link href={`${base}/embalagens/${padrao.id}`} className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
                      Editar
                    </Link>
                    {/* Embalagem é opcional: a única pode sair (e a loja volta à caixa sob medida). */}
                    {canWrite && !outras.length ? (
                      <form action={setActive}>
                        {shared}
                        <input type="hidden" name="package_id" value={padrao.id} />
                        <input type="hidden" name="active" value="0" />
                        <button type="submit" className={styles.buttonSmall}>
                          Arquivar
                        </button>
                      </form>
                    ) : null}
                  </>,
                )}
              </div>
            </Section>
          ) : null}

          <Section title="Outras embalagens" description={outras.length === 1 ? "1 embalagem" : `${outras.length} embalagens`}>
            {outras.length ? (
              <div className={styles.rows}>
                {outras.map((pkg) =>
                  row(
                    pkg,
                    <>
                      <Link href={`${base}/embalagens/${pkg.id}`} className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
                        Editar
                      </Link>
                      <form action={makeDefault}>
                        {shared}
                        <input type="hidden" name="package_id" value={pkg.id} />
                        <button type="submit" className={styles.buttonSmall}>
                          Tornar padrão
                        </button>
                      </form>
                      <form action={setActive}>
                        {shared}
                        <input type="hidden" name="package_id" value={pkg.id} />
                        <input type="hidden" name="active" value="0" />
                        <button type="submit" className={styles.buttonSmall}>
                          Arquivar
                        </button>
                      </form>
                    </>,
                  ),
                )}
              </div>
            ) : (
              <EmptyState
                title="Só a embalagem padrão por enquanto"
                action={
                  canWrite ? (
                    <div className={local.rowForms}>
                      {PACKAGE_PRESETS.filter((p) => p.key === "p" || p.key === "envelope").map((preset) => (
                        <form key={preset.key} action={addPreset}>
                          {shared}
                          <input type="hidden" name="name" value={preset.label} />
                          <input type="hidden" name="kind" value={preset.kind} />
                          <input type="hidden" name="dims" value={preset.inner.join("x")} />
                          <input type="hidden" name="tare" value={preset.tare} />
                          <button type="submit" className={styles.buttonGhost}>
                            Adicionar {preset.label}
                          </button>
                        </form>
                      ))}
                    </div>
                  ) : undefined
                }
              >
                Ter 2 ou 3 tamanhos costuma baratear o frete: pedido pequeno vai em caixa pequena.
              </EmptyState>
            )}
          </Section>

          {arquivadas.length ? (
            <details className={styles.card}>
              <summary>Arquivadas ({arquivadas.length})</summary>
              <div className={styles.rows}>
                {arquivadas.map((pkg) =>
                  row(
                    pkg,
                    <>
                      <form action={setActive}>
                        {shared}
                        <input type="hidden" name="package_id" value={pkg.id} />
                        <input type="hidden" name="active" value="1" />
                        <button type="submit" className={styles.buttonSmall}>
                          Reativar
                        </button>
                      </form>
                      {pkg.rules_count === 0 ? (
                        <form action={deletePackage}>
                          {shared}
                          <input type="hidden" name="package_id" value={pkg.id} />
                          <button type="submit" className={`${styles.buttonSmall} ${styles.buttonDanger}`}>
                            Apagar
                          </button>
                        </form>
                      ) : (
                        <span className={styles.fieldHint}>Usada por {pkg.rules_count} produto(s): não dá para apagar.</span>
                      )}
                    </>,
                  ),
                )}
              </div>
            </details>
          ) : null}
        </>
      )}

      <Section title="Regras de embalagem" description="Valem para todas as embalagens da loja.">
        {canRules ? (
          <details>
            <summary>
              Folga {cmInput(regras.padding_mm) || "0"} cm · flexíveis ocupam {regras.flexible_fill_percent}% · compara até{" "}
              {regras.max_candidates} combinações · {regras.declare_value ? "declara o valor" : "sem seguro"}
            </summary>
            <form action={savePackingRules} className={local.rulesForm}>
              {shared}
              <div className={styles.fields}>
                <label className={styles.field}>
                  Folga por lado (cm)
                  <input name="padding" inputMode="decimal" defaultValue={cmInput(regras.padding_mm) || "0"} />
                  <span className={styles.fieldHint}>Espaço para plástico-bolha ou papel, descontado de toda embalagem.</span>
                </label>
                <label className={styles.field}>
                  Produtos flexíveis ocupam (%)
                  <input name="flexible_fill" type="number" min={50} max={100} defaultValue={regras.flexible_fill_percent} />
                  <span className={styles.fieldHint}>Roupa e rabiola amassam, mas não somem: 85% costuma ser realista.</span>
                </label>
                <label className={styles.field}>
                  Comparar até (combinações)
                  <input name="max_candidates" type="number" min={1} max={4} defaultValue={regras.max_candidates} />
                  <span className={styles.fieldHint}>Cada combinação é uma consulta à transportadora.</span>
                </label>
                <label className={styles.field}>
                  No máximo (volumes por pedido)
                  <input name="max_parcels" type="number" min={1} max={20} defaultValue={regras.max_parcels} />
                </label>
              </div>
              <label className={styles.check}>
                <input type="checkbox" name="declare_value" defaultChecked={regras.declare_value} />
                <span>
                  Declarar o valor dos produtos (seguro)
                  <span className={styles.fieldHint}>
                    Com seguro, a transportadora cobra um pouco mais e indeniza pelo valor declarado se extraviar.
                  </span>
                </span>
              </label>
              <label className={styles.check}>
                <input type="checkbox" name="charge_material" defaultChecked={regras.charge_material} />
                <span>
                  Somar o custo da embalagem ao frete
                  <span className={styles.fieldHint}>Usa o custo que você cadastrou em cada embalagem.</span>
                </span>
              </label>
              <div className={styles.formActions}>
                <button type="submit" className={styles.button}>
                  Salvar regras
                </button>
              </div>
            </form>
          </details>
        ) : (
          <KeyValues
            items={[
              { label: "Folga por lado", value: `${cmInput(regras.padding_mm) || "0"} cm` },
              { label: "Flexíveis ocupam", value: `${regras.flexible_fill_percent}%` },
              { label: "Combinações comparadas", value: String(regras.max_candidates) },
              { label: "Seguro", value: regras.declare_value ? "Declara o valor" : "Sem seguro" },
            ]}
          />
        )}
      </Section>

      {vendaveis.length ? (
        <Section
          id="simulador"
          title="Testar frete"
          description="Escolha produtos e quantidades e veja como o sistema montaria as caixas."
        >
          <form method="get" action={`${base}/embalagens#simulador`} className={local.simLines}>
            {Array.from({ length: SIM_ROWS }, (_, i) => (
              <div key={i} className={local.simLine}>
                <label className={styles.field}>
                  {i === 0 ? "Produto" : <span className={styles.fieldHint}>Outro produto (opcional)</span>}
                  <select name={`p${i}`} defaultValue={pedidos[i]?.productId ?? ""}>
                    <option value="">—</option>
                    {vendaveis.map((p) => (
                      <option key={p.id} value={p.id}>
                        {p.name}
                      </option>
                    ))}
                  </select>
                </label>
                <label className={styles.field}>
                  {i === 0 ? "Quantidade" : <span className={styles.fieldHint}>Qtd.</span>}
                  <input name={`q${i}`} type="number" min={1} max={500} defaultValue={pedidos[i]?.units ?? (i === 0 ? 1 : "")} />
                </label>
              </div>
            ))}
            <label className={styles.field}>
              CEP de destino (opcional)
              <input name="cep" inputMode="numeric" maxLength={9} defaultValue={query.cep ?? ""} placeholder="20040-020" />
              <span className={styles.fieldHint}>
                Com CEP, cada combinação é cotada de verdade na sua conta da transportadora.
              </span>
            </label>
            <div className={styles.formActions}>
              <button type="submit" className={styles.button}>
                Montar as caixas
              </button>
            </div>
          </form>
          {simErro ? <p className={styles.error}>Não deu para simular agora ({simErro}).</p> : null}
          {simulacao ? <SimResult result={simulacao} nomes={nomes} /> : null}
        </Section>
      ) : null}
    </>
  );
}

function SimResult({ result, nomes }: { result: SimulateOut; nomes: Map<string, string> }) {
  return (
    <div id="resultado">
      {result.missing.length ? (
        <p className={styles.note}>
          Sem peso ou medida (ficaram de fora): {result.missing.map((v) => nomes.get(v) ?? v).join(", ")}.
        </p>
      ) : null}
      {result.quote_problem ? <p className={styles.note}>{QUOTE_PROBLEM_TEXT[result.quote_problem] ?? result.quote_problem}</p> : null}
      {result.problem === "too_many_parcels" ? (
        <p className={styles.error}>Passou do limite de volumes por pedido das suas regras de embalagem.</p>
      ) : null}
      {result.plans.map((plano) => (
        <div key={plano.hash} className={local.plan} data-strategy={plano.strategy}>
          <div className={local.planHead}>
            <strong>{STRATEGY_LABEL[plano.strategy] ?? plano.strategy}</strong>
            <span>
              {plano.quoted ? <Pill state="live">Seria cotada</Pill> : <Pill state="off">Fora da cotação</Pill>}{" "}
              {plano.degraded ? <Pill state="warn">Montagem simplificada</Pill> : null}
            </span>
          </div>
          <ol className={local.parcels}>
            {plano.parcels.map((v, i) => (
              <li key={i}>
                <strong>
                  {v.custom
                    ? "Caixa sob medida"
                    : v.oversize
                      ? "Maior que suas embalagens"
                      : v.own
                        ? "Na embalagem do produto"
                        : v.package_name}
                </strong>{" "}
                — {v.custom && v.inner_mm ? `monte com ${dimsLabel(v.inner_mm)} por dentro · ` : ""}
                {dimsLabel(v.outer_mm)} por fora · {weightLabel(v.gross_grams)}
                {v.billable_correios_grams > v.gross_grams
                  ? ` (cobrado como ${weightLabel(v.billable_correios_grams)} nos Correios)`
                  : ""}
                {v.declared ? " · capacidade declarada pela loja" : ""}
                <br />
                {v.items.map((item) => `${item.units}× ${item.name}`).join(", ")}
              </li>
            ))}
          </ol>
          {plano.quotes && plano.quotes.length ? (
            <ul className={local.quotes}>
              {plano.quotes.map((q, i) => (
                <li key={`${q.service_code}-${i}`} data-best={q.best ? "true" : undefined}>
                  <span>
                    {q.carrier} {q.service_name}
                    {q.delivery_min && q.delivery_max ? (
                      <span className={styles.fieldHint}>
                        {" "}
                        · {q.delivery_min === q.delivery_max ? q.delivery_max : `${q.delivery_min} a ${q.delivery_max}`} dias úteis
                      </span>
                    ) : null}
                  </span>
                  <span>
                    {q.price_cents !== null && !q.error ? (
                      <strong>{formatMoney(q.price_cents)}</strong>
                    ) : (
                      <em className={styles.fieldHint}>{q.error ?? "não leva"}</em>
                    )}
                    {q.best ? (
                      <>
                        {" "}
                        <Pill state="live">Mais barata neste serviço</Pill>
                      </>
                    ) : null}
                  </span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={local.estimate}>
              Estimativa só para comparar: Correios {formatEstimate(plano.estimate_cents.correios)} · Jadlog{" "}
              {formatEstimate(plano.estimate_cents.jadlog_package)}. Informe um CEP para ver o preço de verdade.
            </p>
          )}
        </div>
      ))}
    </div>
  );
}

function formatEstimate(cents: number | null | undefined): string {
  return cents === null || cents === undefined ? "não leva" : `~${formatMoney(cents)}`;
}
