/** Customer order shapes and labels (pure, shared by the order pages). */

export interface OrderItem {
  line_no: number;
  product_id: string;
  name: string;
  sku: string;
  quantity: string;
  unit_label: string;
  modifiers: { name: string; price_cents: number }[];
  unit_price_cents: number;
  total_cents: number;
  event: { lot_name?: string; starts_at?: string; venue_name?: string | null } | null;
}

export interface Order {
  id: string;
  number: number;
  status: string;
  currency: string;
  subtotal_cents: number;
  discount_cents: number;
  delivery_fee_cents: number;
  total_cents: number;
  fulfillment_type: string;
  fulfillment_status: string;
  fulfillment: Record<string, unknown> | null;
  scheduled_start: string | null;
  scheduled_end: string | null;
  placed_at: string;
  expires_at: string | null;
  paid_at: string | null;
  cancelled_at: string | null;
  refund_status: string;
  items: OrderItem[];
  timeline: { status: string; at: string; reason: string | null }[];
}

export const ORDER_STATUS_LABEL: Record<string, string> = {
  awaiting_payment: "Aguardando pagamento",
  payment_confirmed: "Pagamento confirmado",
  accepted: "Pedido aceito",
  in_production: "Em preparo",
  ready_for_pickup: "Pronto para retirar",
  shipped: "Saiu para entrega",
  delivered: "Entregue",
  cancelled: "Cancelado",
  failed: "Expirado sem pagamento",
};

export function orderStatusLabel(status: string): string {
  return ORDER_STATUS_LABEL[status] ?? status;
}
