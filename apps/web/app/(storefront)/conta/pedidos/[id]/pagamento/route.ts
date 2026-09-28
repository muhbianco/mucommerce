import { type NextRequest, NextResponse } from "next/server";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { type OrderPayment, snapshotOf } from "@/lib/payments";

const ID = /^[0-9a-f-]{36}$/;
const NO_STORE = { "Cache-Control": "no-store" };

/** Where paying for my order stands, for the order page's poller: statuses only, nothing else. */
export async function GET(
  _request: NextRequest,
  { params }: { params: Promise<{ id: string }> },
): Promise<NextResponse> {
  const { id } = await params;
  if (!ID.test(id)) return NextResponse.json({ error: { code: "not_found" } }, { status: 404, headers: NO_STORE });
  try {
    const state = await customerApi<OrderPayment>(`/checkout/orders/${id}/payment`);
    return NextResponse.json(snapshotOf(state), { headers: NO_STORE });
  } catch (error) {
    if (error instanceof CustomerApiError) {
      return NextResponse.json({ error: { code: error.code } }, { status: error.status, headers: NO_STORE });
    }
    throw error;
  }
}
