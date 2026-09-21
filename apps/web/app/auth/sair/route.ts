import { type NextRequest, NextResponse } from "next/server";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE, customerCookie } from "@/lib/customer-cookies";
import { relativeRedirect } from "@/lib/relative-redirect";
import { sameStoreOrigin } from "@/lib/same-origin";

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
