/**
 * Cor do selo de cada situação, na vitrine.
 *
 * Existe separado de `lib/panel/states.ts` porque o vocabulário é outro. Para o lojista,
 * `accepted` é "aceito, entrou na fila de produção" — informativo. Para quem comprou, é
 * "a loja confirmou o seu pedido" — boa notícia. Misturar os dois mapas é como se escreve
 * a mensagem errada para o público errado.
 *
 * A cor nunca carrega o significado sozinha: todo selo sai com rótulo escrito (WCAG 1.4.1).
 */

export type StorePillState = "ok" | "warn" | "danger" | "info" | "off" | "brand";

/** Disponibilidade de produto, como o cliente lê. */
export const AVAILABILITY_STATE: Record<string, StorePillState> = {
  available: "ok",
  made_to_order: "info",
  sold_out: "danger",
  unavailable: "off",
};

export const EVENT_STATE: Record<string, StorePillState> = {
  on_sale: "ok",
  upcoming: "info",
  sold_out: "danger",
  ended: "off",
  unavailable: "off",
};

export const LOT_STATE: Record<string, StorePillState> = {
  on_sale: "ok",
  upcoming: "info",
  sold_out: "danger",
  ended: "off",
  unavailable: "off",
};

/** Situação do pedido na conta do cliente — não é a do painel do lojista. */
export const CUSTOMER_ORDER_STATE: Record<string, StorePillState> = {
  awaiting_payment: "warn",
  payment_confirmed: "ok",
  // O lojista vê "aceito, entrou na fila"; quem comprou vê "a loja confirmou".
  accepted: "ok",
  in_production: "info",
  ready_for_pickup: "ok",
  shipped: "info",
  delivered: "ok",
  cancelled: "off",
  failed: "danger",
};

export function stateOf(map: Record<string, StorePillState>, status: string): StorePillState {
  return map[status] ?? "off";
}
