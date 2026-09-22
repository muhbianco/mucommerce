"use client";

import Script from "next/script";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState } from "react";

import { payWithCard } from "../../actions";

// Mercado Pago's browser SDK (https://sdk.mercadopago.com/js/v2): the card never touches our
// servers — the Card Payment Brick turns it into a single-use token, which is all we receive.
interface CardData {
  token: string;
  issuer_id: string;
  payment_method_id: string;
  installments: number;
  payer?: { identification?: { type?: string; number?: string } };
}
interface BrickController {
  unmount: () => void;
}
interface MercadoPagoSdk {
  bricks: () => {
    create: (kind: "cardPayment", container: string, settings: unknown) => Promise<BrickController>;
  };
}
declare global {
  interface Window {
    MercadoPago?: new (publicKey: string, options?: { locale?: string }) => MercadoPagoSdk;
  }
}

const CONTAINER = "cardPaymentBrick_container";

export function CardBrick({
  orderId,
  publicKey,
  amountCents,
  maxInstallments,
  payerEmail,
  idempotencyKey,
}: {
  orderId: string;
  publicKey: string;
  amountCents: number;
  maxInstallments: number;
  payerEmail: string | null;
  idempotencyKey: string;
}) {
  const router = useRouter();
  const [sdkReady, setSdkReady] = useState(false);
  const [failed, setFailed] = useState(false);
  const controller = useRef<BrickController | null>(null);

  useEffect(() => {
    if (!sdkReady || !window.MercadoPago) return;
    let cancelled = false;
    const mp = new window.MercadoPago(publicKey, { locale: "pt-BR" });
    mp.bricks()
      .create("cardPayment", CONTAINER, {
        initialization: { amount: amountCents / 100, ...(payerEmail ? { payer: { email: payerEmail } } : {}) },
        customization: { paymentMethods: { maxInstallments } },
        callbacks: {
          onReady: () => undefined,
          onError: () => setFailed(true),
          onSubmit: async (card: CardData) => {
            // The server charges the order's own total; the amount shown here is informative.
            const outcome = await payWithCard({
              orderId,
              idempotencyKey,
              token: card.token,
              paymentMethodId: card.payment_method_id,
              issuerId: card.issuer_id ?? null,
              installments: card.installments,
              identificationType: card.payer?.identification?.type ?? null,
              identificationNumber: card.payer?.identification?.number ?? null,
            });
            router.replace(`/conta/pedidos/${orderId}?${outcome}`);
            router.refresh();
          },
        },
      })
      .then((brick) => {
        if (cancelled) brick.unmount();
        else controller.current = brick;
      })
      .catch(() => setFailed(true));
    return () => {
      cancelled = true;
      controller.current?.unmount();
      controller.current = null;
    };
  }, [sdkReady, publicKey, amountCents, maxInstallments, payerEmail, orderId, idempotencyKey, router]);

  return (
    <div>
      <Script
        src="https://sdk.mercadopago.com/js/v2"
        strategy="afterInteractive"
        onReady={() => setSdkReady(true)}
        onError={() => setFailed(true)}
      />
      {failed ? (
        <p role="alert">Não foi possível carregar o pagamento com cartão. Recarregue a página ou pague com Pix.</p>
      ) : null}
      <div id={CONTAINER} />
    </div>
  );
}
