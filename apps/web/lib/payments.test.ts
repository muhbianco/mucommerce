import { describe, expect, it } from "vitest";

import {
  cardOption,
  changed,
  type OrderPayment,
  isPaymentErrorCode,
  isWaiting,
  paymentError,
  pollDelay,
  qrImageSource,
  safeCheckoutUrl,
} from "./payments";

describe("payment poller rules", () => {
  it("waits only while the order awaits a payment in progress", () => {
    expect(isWaiting({ order_status: "awaiting_payment", payment_status: "requires_action" })).toBe(true);
    expect(isWaiting({ order_status: "awaiting_payment", payment_status: "pending" })).toBe(true);
    expect(isWaiting({ order_status: "awaiting_payment", payment_status: null })).toBe(false);
    expect(isWaiting({ order_status: "payment_confirmed", payment_status: "approved" })).toBe(false);
    expect(isWaiting({ order_status: "failed", payment_status: "requires_action" })).toBe(false);
  });

  it("notices a change in the order or the payment", () => {
    const before = { order_status: "awaiting_payment", payment_status: "requires_action" };
    expect(changed(before, { ...before })).toBe(false);
    expect(changed(before, { ...before, payment_status: "approved" })).toBe(true);
    expect(changed(before, { ...before, order_status: "failed" })).toBe(true);
  });

  it("slows down after two minutes and stops after the Pix window", () => {
    expect(pollDelay(0)).toBe(4_000);
    expect(pollDelay(3 * 60_000)).toBe(10_000);
    expect(pollDelay(36 * 60_000)).toBeNull();
  });
});

describe("what the page shows from the provider", () => {
  it("accepts only a base64 PNG as the QR image", () => {
    expect(qrImageSource("iVBORw0KGgo=")).toBe("data:image/png;base64,iVBORw0KGgo=");
    expect(qrImageSource('x" onerror="alert(1)')).toBeNull();
    expect(qrImageSource(null)).toBeNull();
  });

  it("follows only https payment links", () => {
    expect(safeCheckoutUrl("https://checkout.example.com/p/1")).toBe("https://checkout.example.com/p/1");
    expect(safeCheckoutUrl("javascript:alert(1)")).toBeNull();
    expect(safeCheckoutUrl("http://checkout.example.com")).toBeNull();
    expect(safeCheckoutUrl("not a url")).toBeNull();
  });

  it("explains refusals the customer can act on", () => {
    expect(paymentError("payment_in_progress")).toMatch(/em andamento/);
    expect(paymentError("something_else")).toMatch(/Tente de novo/);
    expect(paymentError(null)).toMatch(/Tente de novo/);
  });

  it("tells a card refusal apart from a typo and from a bank block", () => {
    expect(paymentError("cc_rejected_bad_filled_security_code")).toMatch(/Confira os dados/);
    expect(paymentError("cc_rejected_insufficient_amount")).toMatch(/limite/);
    expect(paymentError("cc_rejected_high_risk")).toMatch(/recusado/);
    expect(isPaymentErrorCode("cc_rejected_high_risk")).toBe(true);
    expect(isPaymentErrorCode("mp_3003")).toBe(true);
    expect(isPaymentErrorCode("cancel_window_closed")).toBe(false);
  });
});

describe("card payments", () => {
  // Built here: no key-shaped literal in the repo (gitleaks scans every file).
  const publicKey = `APP_USR-${"0".repeat(12)}`;
  const state = (overrides: Partial<OrderPayment> = {}): OrderPayment => ({
    order_id: "o",
    order_number: 1,
    order_status: "awaiting_payment",
    total_cents: 3000,
    expires_at: null,
    paid_at: null,
    can_pay: true,
    payment: null,
    payer_email: "a@b.test",
    options: [
      {
        provider: "mercadopago",
        methods: ["pix", "card"],
        mode: "embedded",
        is_default: true,
        public_config: { public_key: publicKey },
        installments_max: 20,
      },
    ],
    ...overrides,
  });

  it("offers the Brick only with a Mercado Pago public key, capping installments", () => {
    expect(cardOption(state())).toEqual({ publicKey, maxInstallments: 12 });
    expect(cardOption(state({ can_pay: false }))).toBeNull();
    const noKey = state();
    noKey.options[0]!.public_config = {};
    expect(cardOption(noKey)).toBeNull();
    const pixOnly = state();
    pixOnly.options[0]!.methods = ["pix"];
    expect(cardOption(pixOnly)).toBeNull();
  });
});
