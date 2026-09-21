import { NextResponse } from "next/server";

/**
 * Redirect to a path on the SAME host the browser asked for. A relative `Location` is resolved
 * by the browser against the store's own URL, whatever origin Next.js thinks it serves
 * (`request.url` is `localhost:<port>` behind some setups).
 */
export function relativeRedirect(path: string, status: 302 | 303 = 303): NextResponse {
  if (!path.startsWith("/") || path.startsWith("//")) throw new Error("relativeRedirect needs a local path");
  return new NextResponse(null, { status, headers: { Location: path, "Cache-Control": "no-store" } });
}
