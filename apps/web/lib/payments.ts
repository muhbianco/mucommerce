/** Customer payment shapes, labels and the poller's rules (pure, shared by the order page). */

export interface PaymentOption {
  provider: string;
  methods: string[];
  mode: string;
  is_default: boolean;
  public_config: Record<string, unknown>;
  installments_max: number;
}

export interface Payment {
  id: string;
  provider: string;
  method: string;
  mode: string;
  status: string;
  amount_cents: number;
  installments: number;
  expires_at: string | null;
  approved_at: string | null;
  pix_copy_paste: string | null;
  pix_qr_base64: string | null;
  checkout_url: string | null;
  failure_code: string | null;
  card: { brand?: string; last_four?: string } | null;
  created_at: string;
}

export interface OrderPayment {
  order_id: string;
  order_number: number;
  order_status: string;
  total_cents: number;
  expires_at: string | null;
  paid_at: string | null;
  can_pay: boolean;
  payment: Payment | null;
  options: PaymentOption[];
  payer_email: string | null;
}

/** What the poller compares: a change in either means the page should re-render. */
export interface PaymentSnapshot {
  order_status: string;
  payment_status: string | null;
}

// Methods started with a plain button; card goes through the provider's browser SDK (Brick).
export const WEB_METHODS = ["pix", "link"] as const;

export const METHOD_LABEL: Record<string, string> = {
  pix: "Pagar com Pix",
  link: "Pagar pelo link",
  card: "Pagar com cartão",
};

export const PAYMENT_STATUS_LABEL: Record<string, string> = {
  pending: "Confirmando com o meio de pagamento…",
  requires_action: "Aguardando o seu pagamento",
  approved: "Pagamento aprovado",
  rejected: "Pagamento recusado",
  cancelled: "Pagamento cancelado",
  expired: "Pagamento expirado",
  partially_refunded: "Parcialmente estornado",
  refunded: "Estornado",
  chargeback: "Contestado no cartão",
};

/** Messages for refusals the customer can act on (API error codes and failure codes). */
export const PAYMENT_ERRORS: Record<string, string> = {
  payment_in_progress: "Já existe um pagamento em andamento para este pedido.",
  order_not_payable: "Este pedido não está mais aguardando pagamento.",
  provider_not_enabled: "Este meio de pagamento não está disponível nesta loja.",
  payment_not_cancellable: "Este pagamento já foi concluído.",
  rate_limited: "Muitas tentativas seguidas. Espere um minuto e tente de novo.",
  cc_rejected_other_reason: "O cartão foi recusado. Tente outro cartão ou outro meio de pagamento.",
  provider_refused: "O meio de pagamento recusou a cobrança. Tente de novo ou use outro meio.",
};

// Card refusals (Mercado Pago `status_detail`): what the customer can do about each.
const CARD_REFUSALS: Record<string, string> = {
  cc_rejected_insufficient_amount: "O cartão não tem limite suficiente. Tente outro cartão ou pague com Pix.",
  cc_rejected_call_for_authorize: "O banco pediu autorização. Fale com o banco do cartão e tente de novo.",
  cc_rejected_card_disabled: "O cartão está desativado. Fale com o banco ou use outro cartão.",
  cc_rejected_invalid_installments: "O cartão não aceita esse número de parcelas.",
  cc_rejected_duplicated_payment: "Um pagamento igual acabou de ser feito. Confira antes de tentar de novo.",
  cc_rejected_max_attempts: "Muitas tentativas com este cartão. Use outro cartão ou pague com Pix.",
};

export function paymentError(code: string | null | undefined): string {
  if (!code) return "Não foi possível concluir o pagamento. Tente de novo.";
  if (PAYMENT_ERRORS[code]) return PAYMENT_ERRORS[code];
  if (CARD_REFUSALS[code]) return CARD_REFUSALS[code];
  if (code.startsWith("cc_rejected_bad_filled")) return "Confira os dados do cartão e tente de novo.";
  if (code.startsWith("cc_rejected") || code.startsWith("rejected_")) {
    return "O pagamento foi recusado. Tente outro cartão ou pague com Pix.";
  }
  return "Não foi possível concluir o pagamento. Tente de novo.";
}

/** Error codes the order page explains with `paymentError` (the rest are order errors). */
export function isPaymentErrorCode(code: string): boolean {
  return (
    code in PAYMENT_ERRORS ||
    code in CARD_REFUSALS ||
    code.startsWith("cc_rejected") ||
    code.startsWith("rejected_") ||
    code.startsWith("mp_") ||
    code === "validation_error"
  );
}

/** Payment statuses in which the page keeps checking by itself. */
const WAITING = new Set(["pending", "requires_action"]);

export function isWaiting(snapshot: PaymentSnapshot): boolean {
  return snapshot.order_status === "awaiting_payment" && WAITING.has(snapshot.payment_status ?? "");
}

export function snapshotOf(state: OrderPayment): PaymentSnapshot {
  return { order_status: state.order_status, payment_status: state.payment?.status ?? null };
}

export function changed(before: PaymentSnapshot, after: PaymentSnapshot): boolean {
  return before.order_status !== after.order_status || before.payment_status !== after.payment_status;
}

/** Poll delay: every 4 s for the first 2 minutes, then every 10 s; stop after 35 minutes. */
export function pollDelay(elapsedMs: number): number | null {
  if (elapsedMs >= 35 * 60_000) return null;
  return elapsedMs < 2 * 60_000 ? 4_000 : 10_000;
}

/** A base64 PNG from the provider, as an <img> source (nothing else is accepted). */
export function qrImageSource(base64: string | null): string | null {
  if (!base64 || !/^[A-Za-z0-9+/]+={0,2}$/.test(base64)) return null;
  return `data:image/png;base64,${base64}`;
}

/** Only https links to the provider's page are followed. */
export function safeCheckoutUrl(url: string | null): string | null {
  if (!url) return null;
  try {
    const parsed = new URL(url);
    if (parsed.protocol === "https:") return parsed.toString();
  } catch {
    return null;
  }
  return null;
}

/** The Mercado Pago option that can take a card here (the Brick needs the store's public key). */
export function cardOption(state: OrderPayment): { publicKey: string; maxInstallments: number } | null {
  if (!state.can_pay) return null;
  const option = state.options.find((o) => o.provider === "mercadopago" && o.methods.includes("card"));
  const publicKey = option?.public_config.public_key;
  if (!option || typeof publicKey !== "string" || !/^[A-Za-z0-9_-]{8,200}$/.test(publicKey)) return null;
  return { publicKey, maxInstallments: Math.min(Math.max(option.installments_max, 1), 12) };
}
