import { cookies } from "next/headers";
import { type ReactNode, Suspense } from "react";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CEP_COOKIE, CEP_FRESH_COOKIE } from "@/lib/customer-cookies";
import { freeShippingGap, money } from "@/lib/store/pricing";
import {
  cepDigits,
  deliveryText,
  estimateBadges,
  estimateProblemText,
  type EstimateResult,
  formatCep,
} from "@/lib/store/shipping";

import { CepInput, EstimateScroll } from "./cep-input";
import { estimateShipping } from "./shipping-actions";
import styles from "./store.module.css";
import { Notice } from "./ui";

/** Busca de CEP dos Correios (conferida em 04/10/2026: "Busca CEP", por endereço). */
const BUSCA_CEP = "https://buscacepinter.correios.com.br/app/endereco/index.php";

export interface EstimateLine {
  variant_id: string;
  quantity: string;
}

/**
 * "Frete e prazo" por CEP, sem login (frete v2, F6). O CEP vai num POST que o guarda num
 * cookie; a página cota ao renderizar, então funciona sem JavaScript e um F5 recota. O preço é
 * o mesmo do carrinho (mesma cotação, mesmo cache), mas sem assinatura: só o carrinho, com o
 * endereço, fecha o pedido.
 */
export async function ShippingEstimate({
  formId,
  back,
  title,
  lines,
  caption,
  fields,
  error,
  subtotalCents,
  freeAboveCents,
  showFreeGap,
  currency,
  pickup,
  item,
  footer,
}: {
  formId: string;
  /** Página para onde o "Calcular" volta (sem query). */
  back: string;
  title: string | null;
  /** O que cotar; vazio = só o formulário (carrinho grande demais, por exemplo). */
  lines: EstimateLine[];
  /** "Para 2× Rabiola", "Para o seu carrinho". */
  caption: string;
  /** Campos do formulário além do CEP (variante, quantidade). */
  fields?: ReactNode;
  error?: string;
  /** O que conta para o frete grátis (o carrinho passa o subtotal depois do cupom). */
  subtotalCents: number;
  freeAboveCents: number | null;
  /** "Faltam R$ X para frete grátis" — o carrinho já tem a barra dele. */
  showFreeGap: boolean;
  currency: string;
  /** Locais de retirada da loja, mostrados como opção grátis. */
  pickup: string[];
  item: "produto" | "carrinho";
  footer?: ReactNode;
}) {
  const jar = await cookies();
  const cep = cepDigits(jar.get(CEP_COOKIE)?.value);
  // Logo depois do "Calcular", a resposta espera a cotação: a pessoa pediu e está esperando, e
  // o resultado por streaming (Suspense) só entra na página com JavaScript. Fora disso (abrir
  // outro produto com o CEP guardado), a página sai na hora e o frete chega depois.
  const pediuAgora = jar.has(CEP_FRESH_COOKIE);
  return (
    <section
      className={styles.estimate}
      id="frete"
      aria-labelledby={title ? `${formId}-titulo` : undefined}
      data-bare={title ? undefined : ""}
    >
      {title ? (
        <h2 id={`${formId}-titulo`} className={styles.estimateTitle}>
          {title}
        </h2>
      ) : null}
      <form id={formId} action={estimateShipping} className={styles.estimateForm}>
        <input type="hidden" name="back" value={back} />
        <label className={styles.estimateCep}>
          CEP
          <CepInput defaultValue={cep ? formatCep(cep) : ""} />
        </label>
        {fields}
        <button type="submit">Calcular</button>
      </form>
      <a className={styles.estimateHelp} href={BUSCA_CEP} target="_blank" rel="noopener noreferrer">
        Não sei meu CEP
      </a>
      {error === "cep" ? <Notice kind="error">Digite os 8 números do CEP.</Notice> : null}
      {cep && lines.length && pediuAgora ? (
        <EstimateResults
          cep={cep}
          lines={lines}
          caption={caption}
          subtotalCents={subtotalCents}
          freeAboveCents={freeAboveCents}
          showFreeGap={showFreeGap}
          currency={currency}
          pickup={pickup}
          item={item}
          footer={footer}
        />
      ) : cep && lines.length ? (
        <Suspense
          fallback={
            <>
              <p className={`muted ${styles.estimateLoading}`} role="status">
                Calculando o frete…
              </p>
              <noscript>
                <p className="muted">Toque em Calcular para ver o frete.</p>
              </noscript>
            </>
          }
        >
          <EstimateResults
            cep={cep}
            lines={lines}
            caption={caption}
            subtotalCents={subtotalCents}
            freeAboveCents={freeAboveCents}
            showFreeGap={showFreeGap}
            currency={currency}
            pickup={pickup}
            item={item}
            footer={footer}
          />
        </Suspense>
      ) : (
        footer
      )}
    </section>
  );
}

async function fetchEstimate(cep: string, lines: EstimateLine[]): Promise<EstimateResult> {
  try {
    return await customerApi<EstimateResult>("/storefront/shipping/estimate", {
      json: { postal_code: cep, lines },
      // A API espera a transportadora por até ~10 s; a página não desiste antes dela.
      timeoutMs: 15000,
    });
  } catch (error) {
    if (error instanceof CustomerApiError) {
      const problem = error.status === 429 ? "rate_limited" : error.status === 422 ? "validation_error" : error.code;
      return { options: [], problem, refusals: [] };
    }
    // Rede ou tempo esgotado até a nossa própria API: vira "tente de novo", não página quebrada.
    if (error instanceof Error && ["TimeoutError", "AbortError", "TypeError"].includes(error.name)) {
      console.warn("Estimativa de frete sem resposta da API", { erro: error.name });
      return { options: [], problem: "unavailable", refusals: [] };
    }
    throw error;
  }
}

async function EstimateResults({
  cep,
  lines,
  caption,
  subtotalCents,
  freeAboveCents,
  showFreeGap,
  currency,
  pickup,
  item,
  footer,
}: {
  cep: string;
  lines: EstimateLine[];
  caption: string;
  subtotalCents: number;
  freeAboveCents: number | null;
  showFreeGap: boolean;
  currency: string;
  pickup: string[];
  item: "produto" | "carrinho";
  footer?: ReactNode;
}) {
  const result = await fetchEstimate(cep, lines);
  const gap = freeShippingGap(subtotalCents, freeAboveCents);
  const gratis = gap === 0;
  const selos = estimateBadges(result.options);
  return (
    <div className={styles.estimateResult}>
      {/* Chave muda a cada cálculo novo: monta de novo e rola até o resultado. */}
      <EstimateScroll key={`${cep}:${lines.map((l) => `${l.variant_id}x${l.quantity}`).join(",")}`} targetId="frete" />
      <p className={styles.estimateFor}>
        {caption} · CEP {formatCep(cep)}
      </p>
      {result.problem ? (
        <Notice kind="warn">
          {estimateProblemText(result.problem, item)}
          {result.refusals?.length ? (
            <ul>
              {result.refusals.map((motivo) => (
                <li key={motivo}>{motivo}</li>
              ))}
            </ul>
          ) : null}
        </Notice>
      ) : result.options.length === 0 ? (
        <Notice kind="warn">{estimateProblemText("no_service", item)}</Notice>
      ) : null}
      {result.options.length || pickup.length ? (
        <ul className={styles.estimateList} aria-label="Opções de frete">
          {result.options.map((option) => (
            <li key={option.service_code} className={styles.estimateOption}>
              <span className={styles.quoteName}>
                {option.service_name} · {option.carrier}
                <span className="muted">{deliveryText(option)}</span>
              </span>
              {(selos[option.service_code] ?? []).map((selo) => (
                <span key={selo} className={styles.tag}>
                  {selo}
                </span>
              ))}
              <span className={styles.quotePrice}>{gratis ? "grátis" : money(option.price_cents, currency)}</span>
            </li>
          ))}
          {pickup.map((lugar) => (
            <li key={lugar} className={styles.estimateOption}>
              <span className={styles.quoteName}>Retirar em {lugar}</span>
              <span className={styles.quotePrice}>grátis</span>
            </li>
          ))}
        </ul>
      ) : null}
      {showFreeGap && result.options.length ? (
        gratis ? (
          <p className={styles.estimateFree}>Frete grátis nesta compra.</p>
        ) : gap ? (
          <p className={styles.estimateFree}>Faltam {money(gap, currency)} para frete grátis.</p>
        ) : null
      ) : null}
      {footer}
    </div>
  );
}
