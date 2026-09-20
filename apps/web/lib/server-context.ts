import { headers } from "next/headers";

import type { StorefrontContext } from "./tenant";

/** Read the tenant context the middleware attached to the request. Server components only. */
export async function getStorefrontContext(): Promise<StorefrontContext | null> {
  const encoded = (await headers()).get("x-tenant-context");
  if (!encoded) return null;
  try {
    return JSON.parse(Buffer.from(encoded, "base64").toString("utf-8")) as StorefrontContext;
  } catch {
    return null;
  }
}
