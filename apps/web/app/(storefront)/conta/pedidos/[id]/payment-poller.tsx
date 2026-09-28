"use client";

import { useRouter } from "next/navigation";
import { useEffect } from "react";

import { changed, isWaiting, type PaymentSnapshot, pollDelay } from "@/lib/payments";

/**
 * While a payment waits for the customer, ask this store's own route (same origin, the session
 * cookie goes along) whether anything changed, and re-render the page when it did. Pauses while
 * the tab is hidden; slows down after two minutes; gives up after the Pix window.
 */
export function PaymentPoller({
  orderId,
  orderStatus,
  paymentStatus,
}: {
  orderId: string;
  orderStatus: string;
  paymentStatus: string | null;
}) {
  const router = useRouter();

  useEffect(() => {
    const initial: PaymentSnapshot = { order_status: orderStatus, payment_status: paymentStatus };
    if (!isWaiting(initial)) return;
    const started = Date.now();
    let timer: ReturnType<typeof setTimeout> | undefined;
    let stopped = false;

    const schedule = () => {
      const delay = pollDelay(Date.now() - started);
      if (delay !== null && !stopped) timer = setTimeout(tick, delay);
    };
    const tick = async () => {
      if (document.visibilityState === "hidden") return schedule();
      try {
        const response = await fetch(`/conta/pedidos/${orderId}/pagamento`, { cache: "no-store" });
        if (response.ok) {
          const now = (await response.json()) as PaymentSnapshot;
          if (changed(initial, now)) {
            router.refresh();
            return;
          }
        }
      } catch {
        // Offline for a moment: the next tick tries again.
      }
      schedule();
    };
    schedule();
    return () => {
      stopped = true;
      if (timer) clearTimeout(timer);
    };
  }, [orderId, orderStatus, paymentStatus, router]);

  return null;
}
