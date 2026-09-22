import "server-only";

import { cookies, headers } from "next/headers";

import { CUSTOMER_SESSION_COOKIE } from "./customer-cookies";

/** An error envelope from api-commerce: `{"error": {"code", "message"}}`. */
export class CustomerApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message);
  }
}

interface CustomerApiInit {
  method?: string;
  json?: unknown;
  /** Session token to forward; defaults to the store's session cookie. */
  session?: string | null;
  /** Extra headers (e.g. Idempotency-Key). */
  headers?: Record<string, string>;
  /** Default 8 s; payments wait longer for the provider. */
  timeoutMs?: number;
}

/**
 * Server-side call to api-commerce for the store's customer: `X-Tenant-Host` (set by the
 * middleware) picks the store, the web token authenticates the web, and the customer's session
 * travels in `X-Customer-Session` (the API honours it only with the web token). The browser's
 * address and user agent go along for rate limits and audit.
 */
export async function customerApi<T>(path: string, init: CustomerApiInit = {}): Promise<T> {
  const base = process.env.COMMERCE_API_INTERNAL_URL ?? "http://127.0.0.1:8000";
  const incoming = await headers();
  const session =
    init.session === undefined ? (await cookies()).get(CUSTOMER_SESSION_COOKIE)?.value : init.session;
  const requestHeaders: Record<string, string> = {
    Accept: "application/json",
    "X-Internal-Token": process.env.INTERNAL_TOKEN_WEB ?? "",
    "X-Tenant-Host": incoming.get("x-tenant-host") ?? "",
  };
  if (session) requestHeaders["X-Customer-Session"] = session;
  Object.assign(requestHeaders, init.headers ?? {});
  const forwardedFor = incoming.get("x-forwarded-for");
  if (forwardedFor) requestHeaders["X-Forwarded-For"] = forwardedFor;
  const userAgent = incoming.get("user-agent");
  if (userAgent) requestHeaders["User-Agent"] = userAgent;
  let body: string | undefined;
  if (init.json !== undefined) {
    body = JSON.stringify(init.json);
    requestHeaders["Content-Type"] = "application/json";
  }

  const response = await fetch(`${base}/api/v1${path}`, {
    method: init.method ?? (body === undefined ? "GET" : "POST"),
    headers: requestHeaders,
    body,
    cache: "no-store",
    signal: AbortSignal.timeout(init.timeoutMs ?? 8000),
  });
  if (response.status === 204) return undefined as T;
  const data = (await response.json().catch(() => ({}))) as { error?: { code?: string; message?: string } };
  if (!response.ok) {
    throw new CustomerApiError(response.status, data.error?.code ?? "error", data.error?.message ?? "Erro");
  }
  return data as T;
}
