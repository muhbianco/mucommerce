import { describe, expect, it } from "vitest";

import {
  cardOption,
  METHOD_LABEL,
  changed,
  type OrderPayment,
  isPaymentErrorCode,
  isWaiting,
  paymentError,
  pollDelay,
  qrImageSource,
  safeCheckoutUrl,
  shouldAutoStart,
  webPayChoices,
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

  it("does not fire the Pix on its own when the store also takes cards", () => {
    // A loja do Silvio: Pix e cartão configurados, e só o Pix aparecia. A página disparava o
    // Pix ao abrir; o Pix em pé derruba `can_pay`, e com isso o cartão sumia para sempre.
    const comCartao = state();
    expect(webPayChoices(comCartao).map((c) => c.method)).toEqual(["pix"]);
    expect(shouldAutoStart(comCartao)).toBe(false);
  });

  it("still fires on its own when Pix is the only way to pay", () => {
    const soPix = state();
    soPix.options[0]!.methods = ["pix"];
    expect(shouldAutoStart(soPix)).toBe(true);
  });

  it("never fires with a payment already standing, even a refused one", () => {
    const soPix = state();
    soPix.options[0]!.methods = ["pix"];
    soPix.payment = { id: "p", status: "rejected" } as OrderPayment["payment"];
    expect(shouldAutoStart(soPix)).toBe(false);
  });

  it("puts Pix first for Mercado Pago and keeps the link for the others", () => {
    // Pix é o meio prioritário no MP: cai na conta na hora e não tira o cliente da loja. Quem
    // não faz checkout aqui dentro (InfinitePay, PagBank) só tem link, e o botão diz isso.
    const duas = state();
    duas.options.push({
      provider: "pagbank",
      methods: ["link"],
      mode: "redirect",
      is_default: false,
      public_config: {},
      installments_max: 1,
    });
    expect(webPayChoices(duas).map((c) => `${c.provider}:${c.method}`)).toEqual([
      "mercadopago:pix",
      "pagbank:link",
    ]);
    expect(METHOD_LABEL.pix).toBe("Pagar com Pix");
    expect(METHOD_LABEL.link).toBe("Pagar com link");
  });

  it("offers nothing to fire once a payment is in progress", () => {
    // `can_pay` falso zera as opções no servidor: a tela não tem o que disparar nem oferecer.
    const emAndamento = state({ can_pay: false, options: [] });
    expect(webPayChoices(emAndamento)).toEqual([]);
    expect(shouldAutoStart(emAndamento)).toBe(false);
  });
});
