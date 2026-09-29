import { randomUUID } from "node:crypto";

import {
  cardOption,
  METHOD_LABEL,
  type OrderPayment,
  PAYMENT_STATUS_LABEL,
  paymentError,
  qrImageSource,
} from "@/lib/payments";

import { Notice, Section } from "../../../_store/ui";
import styles from "../../../_store/store.module.css";
import { cancelPayment, checkPayment } from "../../actions";

import { CardBrick } from "./card-brick";
import { CopyButton } from "./copy-button";
import { payChoices } from "./pay-box";
import { PaymentPoller } from "./payment-poller";

/**
 * Como a pessoa paga este pedido e onde o pagamento está (renderizado no servidor).
 *
 * A tela tem uma ação principal por vez. Com o Pix em pé, o que ela precisa é copiar o código —
 * "já paguei" é apoio e "pagar de outro jeito" é saída, então nenhum dos dois pode ter o mesmo
 * peso visual do primeiro. Três botões iguais empilhados não são escolha, são indecisão.
 */
export function PaymentSection({
  state,
  when,
}: {
  state: OrderPayment;
  when: (iso: string) => string;
}) {
  const payment = state.payment;
  const waiting = payment?.status === "requires_action" || payment?.status === "pending";
  const qr = qrImageSource(payment?.pix_qr_base64 ?? null);
  const card = cardOption(state);
  const pixOpen =
    payment?.status === "requires_action" && payment.method === "pix" && payment.pix_copy_paste;

  return (
    <Section title="Pagamento" id="pagamento" variant="card">
      {payment && !(state.can_pay && payment.status === "cancelled") ? (
        <p>
          <strong>{PAYMENT_STATUS_LABEL[payment.status] ?? payment.status}</strong>
          {payment.status === "approved" && payment.card?.last_four
            ? ` — cartão ${payment.card.brand ?? ""} final ${payment.card.last_four}`
            : ""}
        </p>
      ) : null}
      {payment?.status === "rejected" && state.can_pay ? (
        <Notice kind="error">{paymentError(payment.failure_code)}</Notice>
      ) : null}

      {pixOpen && payment?.pix_copy_paste ? (
        <>
          <p>
            Abra o app do seu banco e pague com Pix
            {payment.expires_at ? ` até ${when(payment.expires_at)}` : ""}. A confirmação aparece
            aqui sozinha.
          </p>
          <div className={styles.pixPanel}>
            {qr ? (
              <div className={styles.pixQr}>
                {/* eslint-disable-next-line @next/next/no-img-element -- a data: QR code, nothing to optimize */}
                <img src={qr} alt="QR Code do Pix" width={200} height={200} />
              </div>
            ) : null}
            <div className={styles.pixCode}>
              <label className={styles.field}>
                Pix copia e cola
                <textarea
                  className={styles.pixCodeValue}
                  readOnly
                  rows={4}
                  value={payment.pix_copy_paste}
                  aria-describedby="pix-dica"
                />
              </label>
              <span className={styles.fieldHint} id="pix-dica">
                Cole no app do banco, na opção Pix copia e cola.
              </span>
              <CopyButton text={payment.pix_copy_paste} />
            </div>
          </div>
        </>
      ) : null}

      {waiting && payment ? (
        <div className={styles.payActions}>
          <form action={checkPayment}>
            <input type="hidden" name="order_id" value={state.order_id} />
            <input type="hidden" name="payment_id" value={payment.id} />
            <button type="submit" className="button">
              Já paguei
            </button>
          </form>
          <form action={cancelPayment}>
            <input type="hidden" name="order_id" value={state.order_id} />
            <input type="hidden" name="payment_id" value={payment.id} />
            <button type="submit" className={styles.linkButton}>
              Pagar de outro jeito
            </button>
          </form>
          <PaymentPoller
            orderId={state.order_id}
            orderStatus={state.order_status}
            paymentStatus={payment.status}
          />
        </div>
      ) : null}

      {card ? (
        <details className={styles.payAlt}>
          <summary className={styles.payAltSummary}>{METHOD_LABEL.card}</summary>
          <div className={styles.payAltBody}>
            <CardBrick
              orderId={state.order_id}
              publicKey={card.publicKey}
              amountCents={state.total_cents}
              maxInstallments={card.maxInstallments}
              payerEmail={state.payer_email}
              idempotencyKey={randomUUID()}
            />
          </div>
        </details>
      ) : null}
      {state.can_pay && !payChoices(state).length && !card ? (
        <Notice kind="info">
          Esta loja ainda não recebe pagamentos online. Fale com a loja para combinar.
        </Notice>
      ) : null}
    </Section>
  );
}
