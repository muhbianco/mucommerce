/**
 * Cor do selo (`Pill`) de cada situação, num lugar só: o painel inteiro fala a mesma língua.
 * live = tudo certo · pending = esperando alguém · warn = precisa de atenção · off = parado
 * info = informativo, sem juízo.
 */
export type PillState = "live" | "pending" | "warn" | "off" | "info";

export const PRODUCT_STATE: Record<string, PillState> = {
  draft: "pending",
  active: "live",
  paused: "warn",
  inactive: "off",
  archived: "off",
};

export const ORDER_STATE: Record<string, PillState> = {
  awaiting_payment: "pending",
  payment_confirmed: "live",
  accepted: "info",
  in_production: "info",
  ready_for_pickup: "info",
  shipped: "info",
  delivered: "live",
  cancelled: "off",
  failed: "off",
};

export const PAYMENT_STATE: Record<string, PillState> = {
  pending: "pending",
  requires_action: "pending",
  approved: "live",
  rejected: "warn",
  cancelled: "off",
  expired: "off",
  partially_refunded: "info",
  refunded: "off",
  chargeback: "warn",
};

export const REFUND_STATE: Record<string, PillState> = {
  requested: "pending",
  approved: "pending",
  processing: "pending",
  completed: "live",
  failed: "warn",
  rejected: "off",
};

export function stateOf(map: Record<string, PillState>, status: string): PillState {
  return map[status] ?? "off";
}

export const SHIPMENT_STATE: Record<string, PillState> = {
  creating: "pending",
  purchased: "info",
  posted: "info",
  in_transit: "info",
  delivered: "live",
  returned: "warn",
  cancelled: "off",
  failed: "off",
};
