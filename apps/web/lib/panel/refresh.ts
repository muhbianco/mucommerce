import type { TokenPair } from "./token";

/**
 * Single-flight refresh. The API rotates refresh tokens and treats a reused one as theft (it
 * revokes the whole session). A page load fans out into several requests (document, RSC,
 * server actions) that may all carry the same expiring session; without this, each would
 * refresh and every one after the first would log the user out.
 *
 * Results are kept briefly per refresh token so requests arriving just after the rotation,
 * still carrying the old cookie, reuse the new pair instead of calling the API again.
 */
export type RefreshResult =
  | { kind: "renewed"; pair: TokenPair }
  // The API refused the refresh token (expired, revoked, reused): the session is over.
  | { kind: "rejected" }
  // Timeout / 5xx / network: keep the cookies, the next request tries again.
  | { kind: "unavailable" };

export type RefreshFetcher = (refreshToken: string) => Promise<RefreshResult>;

const REUSE_MS = 15_000;
const inflight = new Map<string, { at: number; promise: Promise<RefreshResult> }>();

export function refreshOnce(
  refreshToken: string,
  fetcher: RefreshFetcher,
  nowMs: number = Date.now(),
): Promise<RefreshResult> {
  for (const [key, entry] of inflight) {
    if (nowMs - entry.at > REUSE_MS) inflight.delete(key);
  }
  const hit = inflight.get(refreshToken);
  if (hit) return hit.promise;
  const promise = fetcher(refreshToken).catch((): RefreshResult => ({ kind: "unavailable" }));
  inflight.set(refreshToken, { at: nowMs, promise });
  // An outage must not be cached: the next request should retry the API.
  void promise.then((result) => {
    if (result.kind === "unavailable") inflight.delete(refreshToken);
  });
  return promise;
}

export function clearRefreshCache(): void {
  inflight.clear();
}

/** Calls the API from the middleware. `forwardedFor` keeps the client IP in the API's audit log. */
export async function callRefresh(
  refreshToken: string,
  forwardedFor: string | null,
): Promise<RefreshResult> {
  const base = process.env.COMMERCE_API_INTERNAL_URL ?? "http://127.0.0.1:8000";
  const headers: Record<string, string> = { "Content-Type": "application/json" };
  if (forwardedFor) headers["X-Forwarded-For"] = forwardedFor;
  const response = await fetch(`${base}/api/v1/auth/refresh`, {
    method: "POST",
    headers,
    body: JSON.stringify({ refresh_token: refreshToken }),
    cache: "no-store",
    signal: AbortSignal.timeout(5000),
  });
  if (response.ok) return { kind: "renewed", pair: (await response.json()) as TokenPair };
  // 401 = bad/expired/reused token; 422 = malformed cookie. Both end the session.
  if (response.status === 401 || response.status === 422) return { kind: "rejected" };
  return { kind: "unavailable" };
}
