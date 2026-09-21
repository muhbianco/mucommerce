import { type NextRequest, NextResponse } from "next/server";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE, customerCookie } from "@/lib/customer-cookies";
import { relativeRedirect } from "@/lib/relative-redirect";

/** The POST came from a page of this same store host (Sec-Fetch-Site, else Origin vs Host). */
function sameStoreOrigin(request: NextRequest): boolean {
  if (request.headers.get("sec-fetch-site") === "same-origin") return true;
  const origin = request.headers.get("origin");
  const host = request.headers.get("host");
  if (!origin || !host) return false;
  try {
    return new URL(origin).host === host;
  } catch {
    return false;
  }
}

/** Sign out of this store. POST from the store's own pages only (Origin check). */
export async function POST(request: NextRequest): Promise<NextResponse> {
  if (!sameStoreOrigin(request)) {
    return NextResponse.json({ error: { code: "csrf_origin" } }, { status: 403 });
  }
  try {
    await customerApi<void>("/me/logout", { json: {} });
  } catch (error) {
    // The cookie goes away regardless; an expired session is already signed out.
    if (!(error instanceof CustomerApiError)) console.error("customer logout failed", error);
  }
  const response = relativeRedirect("/");
  response.cookies.set(CUSTOMER_SESSION_COOKIE, "", customerCookie(0));
  return response;
}
