import { randomUUID } from "node:crypto";

import {
  cardOption,
  METHOD_LABEL,
  type OrderPayment,
  PAYMENT_STATUS_LABEL,
  paymentError,
  qrImageSource,
} from "@/lib/payments";

import { cancelPayment, checkPayment } from "../../actions";

import { CardBrick } from "./card-brick";
import { payChoices } from "./pay-box";
import { CopyButton } from "./copy-button";
import { PaymentPoller } from "./payment-poller";

/** How the customer pays this order, and where the payment stands (server-rendered). */
export function PaymentSection({ state, when }: { state: OrderPayment; when: (iso: string) => string }) {
  const payment = state.payment;
  const waiting = payment?.status === "requires_action" || payment?.status === "pending";
  const qr = qrImageSource(payment?.pix_qr_base64 ?? null);
  const card = cardOption(state);

  return (
    <section aria-labelledby="pagamento">
      <h2 id="pagamento">Pagamento</h2>
      {payment && !(state.can_pay && payment.status === "cancelled") ? (
        <p>
          <strong>{PAYMENT_STATUS_LABEL[payment.status] ?? payment.status}</strong>
          {payment.status === "approved" && payment.card?.last_four
            ? ` — cartão ${payment.card.brand ?? ""} final ${payment.card.last_four}`
            : ""}
        </p>
      ) : null}
      {payment?.status === "rejected" && state.can_pay ? <p role="alert">{paymentError(payment.failure_code)}</p> : null}

      {payment?.status === "requires_action" && payment.method === "pix" && payment.pix_copy_paste ? (
        <div>
          <p>
            Abra o app do seu banco e pague com Pix
            {payment.expires_at ? ` até ${when(payment.expires_at)}` : ""}. A confirmação aparece aqui sozinha.
          </p>
          {qr ? (
            // eslint-disable-next-line @next/next/no-img-element -- a data: QR code, nothing to optimize
            <img src={qr} alt="QR Code do Pix" width={220} height={220} />
          ) : null}
          <p>
            <label>
              Pix copia e cola
              <br />
              <textarea readOnly rows={3} cols={40} value={payment.pix_copy_paste} />
            </label>
          </p>
          <CopyButton text={payment.pix_copy_paste} />
        </div>
      ) : null}


      {waiting && payment ? (
        <div>
          <form action={checkPayment}>
            <input type="hidden" name="order_id" value={state.order_id} />
            <input type="hidden" name="payment_id" value={payment.id} />
            <button type="submit">Já paguei</button>
          </form>
          <form action={cancelPayment}>
            <input type="hidden" name="order_id" value={state.order_id} />
            <input type="hidden" name="payment_id" value={payment.id} />
            <button type="submit" className="muted">
              Pagar de outro jeito
            </button>
          </form>
          <PaymentPoller orderId={state.order_id} orderStatus={state.order_status} paymentStatus={payment.status} />
        </div>
      ) : null}


      {card ? (
        <details>
          <summary>{METHOD_LABEL.card}</summary>
          <CardBrick
            orderId={state.order_id}
            publicKey={card.publicKey}
            amountCents={state.total_cents}
            maxInstallments={card.maxInstallments}
            payerEmail={state.payer_email}
            idempotencyKey={randomUUID()}
          />
        </details>
      ) : null}
      {state.can_pay && !payChoices(state).length && !card ? (
        <p className="muted">Esta loja ainda não recebe pagamentos online. Fale com a loja para combinar.</p>
      ) : null}
    </section>
  );
}
