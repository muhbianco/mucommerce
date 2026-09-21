import type { NextRequest } from "next/server";

/** The POST came from a page of this same store host (Sec-Fetch-Site, else Origin vs Host). */
export function sameStoreOrigin(request: NextRequest): boolean {
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
