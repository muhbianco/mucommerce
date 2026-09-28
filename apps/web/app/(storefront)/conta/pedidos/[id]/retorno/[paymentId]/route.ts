import { type NextRequest, NextResponse } from "next/server";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { relativeRedirect } from "@/lib/relative-redirect";

const ID = /^[0-9a-f-]{36}$/;
const TOKEN = /^[A-Za-z0-9_.-]{1,64}$/;

/**
 * The customer back from the provider's payment page (InfinitePay appends `transaction_nsu` and
 * `slug`). Those values only let the API ask the provider about this payment — they are never
 * taken as proof — so reading them on a GET is safe; the answer shows on the order page.
 */
export async function GET(
  request: NextRequest,
  { params }: { params: Promise<{ id: string; paymentId: string }> },
): Promise<NextResponse> {
  const { id, paymentId } = await params;
  if (!ID.test(id) || !ID.test(paymentId)) return relativeRedirect("/conta/pedidos");
  const back = `/conta/pedidos/${id}`;
  const query = request.nextUrl.searchParams;
  const transaction = query.get("transaction_nsu") ?? "";
  const slug = query.get("slug") ?? "";
  const hints = TOKEN.test(transaction) && TOKEN.test(slug) ? { transaction_nsu: transaction, slug } : {};
  try {
    await customerApi(`/checkout/payments/${paymentId}/check`, { json: hints, timeoutMs: 20000 });
  } catch (error) {
    if (!(error instanceof CustomerApiError)) throw error;
    if (error.status === 401) return relativeRedirect(`/entrar?next=${encodeURIComponent(back)}`);
    // Anything else (rate limit, provider down): the page and its poller carry on from here.
  }
  return relativeRedirect(`${back}?ok=retorno`);
}
