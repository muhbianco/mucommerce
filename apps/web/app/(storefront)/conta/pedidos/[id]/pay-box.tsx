import { randomUUID } from "node:crypto";

import { METHOD_LABEL, type OrderPayment, safeCheckoutUrl, WEB_METHODS } from "@/lib/payments";

import styles from "../../../_store/store.module.css";
import { startPayment } from "../../actions";
import { AutoSubmit } from "./pay-now";

/**
 * Pagar, ao lado do total.
 *
 * Antes isto era um botão pequeno no meio da coluna da esquerda, embaixo do estado do
 * pagamento — o lugar onde a pessoa lê o que aconteceu, não onde ela age. A ação de pagar mora
 * junto do valor, que é o padrão de qualquer checkout e o que a pessoa procura com o olho.
 *
 * **Um jeito só de pagar dispara sozinho.** Quando a loja oferece uma única forma e o pedido
 * ainda não tem pagamento, não há escolha a fazer: pedir um clique só para confirmar o óbvio é
 * um passo a mais entre a pessoa e o dinheiro dela saindo. O disparo acontece **uma vez por
 * pedido**, nunca por visita — depois dele existe um pagamento, e esta caixa passa a mostrar
 * "continuar", não a criar outro.
 *
 * Com mais de uma forma, a escolha continua sendo dela: disparar uma sozinho escolheria o meio
 * de pagamento no lugar de quem paga.
 */

const PAY_FORM_ID = "pagar-agora";

export function payChoices(state: OrderPayment): {
  provider: string;
  method: (typeof WEB_METHODS)[number];
  surcharge: number;
}[] {
  if (!state.can_pay) return [];
  return state.options.flatMap((option) =>
    option.methods
      .filter((method): method is (typeof WEB_METHODS)[number] =>
        (WEB_METHODS as readonly string[]).includes(method),
      )
      .map((method) => ({
        provider: option.provider,
        method,
        surcharge: option.surcharge_cents?.[method] ?? 0,
      })),
  );
}

export function PayBox({ state, total }: { state: OrderPayment; total: string }) {
  const payment = state.payment;
  const link = safeCheckoutUrl(payment?.checkout_url ?? null);

  // Pagamento em pé com endereço do provedor: o que falta é ir até lá, não criar outro.
  if (payment?.status === "requires_action" && link) {
    return (
      <div className={styles.payBox}>
        <a className={`button ${styles.payButton}`} href={link} rel="noopener noreferrer">
          Continuar para o pagamento
        </a>
        <p className={styles.payHint}>Abre a página segura da operadora. A confirmação volta sozinha.</p>
      </div>
    );
  }

  const choices = payChoices(state);
  if (!choices.length) return null;

  // Só dispara sozinho quando não há escolha a fazer **e** ainda não há pagamento. Com um
  // pagamento recusado em pé, a pessoa decide se tenta de novo: repetir sozinho uma recusa é
  // insistir num cartão que o banco negou.
  const sozinho = choices.length === 1 && !payment;

  return (
    <div className={styles.payBox}>
      {choices.map(({ provider, method, surcharge }, i) => (
        <form
          key={`${provider}:${method}`}
          id={i === 0 && sozinho ? PAY_FORM_ID : undefined}
          action={startPayment}
        >
          <input type="hidden" name="order_id" value={state.order_id} />
          <input type="hidden" name="provider" value={provider} />
          <input type="hidden" name="method" value={method} />
          <input type="hidden" name="idempotency_key" value={randomUUID()} />
          <button type="submit" className={`button ${styles.payButton}`}>
            {choices.length === 1 ? `Pagar ${total}` : (METHOD_LABEL[method] ?? method)}
            {surcharge > 0 ? ` (+ ${money(surcharge)})` : ""}
          </button>
        </form>
      ))}
      {sozinho ? <AutoSubmit formId={PAY_FORM_ID} /> : null}
      {choices.length === 1 ? (
        <p className={styles.payHint}>
          {METHOD_LABEL[choices[0]!.method] ?? choices[0]!.method}. Abrimos o pagamento para você.
        </p>
      ) : null}
    </div>
  );
}

/** Reais, para a linha do acréscimo. A moeda das lojas é sempre BRL hoje. */
const money = (cents: number): string =>
  new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" }).format(cents / 100);
