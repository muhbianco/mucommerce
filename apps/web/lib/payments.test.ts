import { describe, expect, it } from "vitest";

import { changed, isWaiting, paymentError, pollDelay, qrImageSource, safeCheckoutUrl } from "./payments";

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
});
