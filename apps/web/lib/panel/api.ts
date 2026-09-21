import "server-only";

import { cookies, headers } from "next/headers";
import { redirect } from "next/navigation";

import { ACCESS_COOKIE } from "./token";
import type { Me } from "./types";

/** An error envelope from api-commerce: `{"error": {"code", "message", "details"}}`. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
    readonly details: Record<string, unknown> = {},
  ) {
    super(message);
  }
}

interface ApiInit {
  method?: string;
  json?: unknown;
  form?: URLSearchParams;
  /** Defaults to the session cookie; `null` sends no Authorization (login). */
  token?: string | null;
  idempotencyKey?: string;
}

/**
 * Server-side call to api-commerce over the internal network. Forwards the browser's
 * X-Forwarded-For (set by Traefik) so login rate limits and audit see the real client.
 */
export async function api<T>(path: string, init: ApiInit = {}): Promise<T> {
  const base = process.env.COMMERCE_API_INTERNAL_URL ?? "http://127.0.0.1:8000";
  const incoming = await headers();
  const token =
    init.token === undefined ? (await cookies()).get(ACCESS_COOKIE)?.value : init.token;

  const requestHeaders: Record<string, string> = { Accept: "application/json" };
  if (token) requestHeaders.Authorization = `Bearer ${token}`;
  const forwardedFor = incoming.get("x-forwarded-for");
  if (forwardedFor) requestHeaders["X-Forwarded-For"] = forwardedFor;
  const userAgent = incoming.get("user-agent");
  if (userAgent) requestHeaders["User-Agent"] = userAgent;
  if (init.idempotencyKey) requestHeaders["Idempotency-Key"] = init.idempotencyKey;

  let body: string | URLSearchParams | undefined;
  if (init.json !== undefined) {
    body = JSON.stringify(init.json);
    requestHeaders["Content-Type"] = "application/json";
  } else if (init.form) {
    body = init.form;
    requestHeaders["Content-Type"] = "application/x-www-form-urlencoded";
  }

  const response = await fetch(`${base}/api/v1${path}`, {
    method: init.method ?? (body === undefined ? "GET" : "POST"),
    headers: requestHeaders,
    body,
    cache: "no-store",
    signal: AbortSignal.timeout(8000),
  });
  if (response.status === 204) return undefined as T;
  const data = (await response.json().catch(() => ({}))) as {
    error?: { code?: string; message?: string; details?: Record<string, unknown> };
  };
  if (!response.ok) {
    const error = data.error ?? {};
    throw new ApiError(
      response.status,
      error.code ?? `http_${response.status}`,
      error.message ?? "Falha ao falar com a API.",
      error.details ?? {},
    );
  }
  return data as T;
}

/** The signed-in staff user, or a redirect to the login page. */
export async function requireMe(): Promise<Me> {
  try {
    return await api<Me>("/auth/me");
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) redirect("/entrar");
    throw error;
  }
}
