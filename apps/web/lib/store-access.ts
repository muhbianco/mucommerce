import "server-only";

import { notFound, redirect } from "next/navigation";

import type { StoreResult } from "./storefront-api";

/**
 * A catalog page cannot render without its data: send the visitor where they can go on.
 * Not signed in → /entrar; signed in without approval (or blocked) → /acesso-pendente; gone → 404.
 */
export function requireCatalog<T>(result: StoreResult<T>, next: string): T {
  if (result.kind === "ok") return result.data;
  if (result.kind === "login_required") redirect(`/entrar?next=${encodeURIComponent(next)}`);
  if (result.kind === "not_found") notFound();
  redirect(`/acesso-pendente?next=${encodeURIComponent(next)}`);
}
