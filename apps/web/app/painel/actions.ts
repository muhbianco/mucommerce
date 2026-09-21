"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { api, ApiError } from "@/lib/panel/api";
import {
  ACCESS_COOKIE,
  cookieOptions,
  EXPIRED_COOKIE,
  REFRESH_COOKIE,
  REFRESH_TTL_SECONDS,
  safeReturnPath,
  type TokenPair,
} from "@/lib/panel/token";
import { ACCESS_MODES, type Tenant, type TenantPanelContext } from "@/lib/panel/types";

// Server Actions only run for same-origin POSTs (Next checks Origin against Host), which is the
// CSRF protection for every mutation below. Errors come back as ?erro=<code> on a redirect so
// the pages work without client JavaScript.

function field(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

function errorCode(error: unknown): string {
  if (error instanceof ApiError) return error.code;
  throw error;
}

export async function login(form: FormData): Promise<void> {
  const next = safeReturnPath(field(form, "next"));
  let pair: TokenPair;
  try {
    pair = await api<TokenPair>("/auth/token", {
      token: null,
      form: new URLSearchParams({
        username: field(form, "email"),
        password: String(form.get("password") ?? ""),
      }),
    });
  } catch (error) {
    const code = errorCode(error) === "rate_limited" ? "limite" : "credenciais";
    redirect(`/entrar?erro=${code}`);
  }
  const jar = await cookies();
  jar.set(ACCESS_COOKIE, pair.access_token, cookieOptions(pair.expires_in));
  jar.set(REFRESH_COOKIE, pair.refresh_token, cookieOptions(REFRESH_TTL_SECONDS));
  redirect(next);
}

export async function logout(): Promise<void> {
  const jar = await cookies();
  const refreshToken = jar.get(REFRESH_COOKIE)?.value;
  try {
    await api("/auth/logout", { json: refreshToken ? { refresh_token: refreshToken } : {} });
  } catch (error) {
    // The session ends locally either way; an expired access token is the common case here.
    if (!(error instanceof ApiError)) throw error;
  }
  jar.set(ACCESS_COOKIE, "", EXPIRED_COOKIE);
  jar.set(REFRESH_COOKIE, "", EXPIRED_COOKIE);
  redirect("/entrar");
}

export async function createTenant(form: FormData): Promise<void> {
  let tenant: Tenant;
  try {
    tenant = await api<Tenant>("/ops/tenants", {
      json: { slug: field(form, "slug"), name: field(form, "name") },
      // One key per rendered form: a double submit replays instead of creating twice.
      idempotencyKey: field(form, "idempotency_key") || undefined,
    });
  } catch (error) {
    redirect(`/ops/tenants?erro=${errorCode(error)}`);
  }
  redirect(`/ops/tenants/${encodeURIComponent(tenant.id)}`);
}

export async function setTenantStatus(form: FormData): Promise<void> {
  const id = encodeURIComponent(field(form, "tenant_id"));
  let outcome = "ok=status";
  try {
    await api(`/ops/tenants/${id}/status`, { json: { status: field(form, "status") } });
  } catch (error) {
    outcome = `erro=${errorCode(error)}`;
  }
  redirect(`/ops/tenants/${id}?${outcome}`);
}

export async function setFeatures(form: FormData): Promise<void> {
  const id = encodeURIComponent(field(form, "tenant_id"));
  const keys = field(form, "keys").split(",").filter(Boolean);
  // Unchecked boxes are not submitted: every listed key is sent, checked or not.
  const flags = Object.fromEntries(keys.map((key) => [key, form.get(`flag:${key}`) === "on"]));
  let outcome = "ok=flags";
  try {
    await api(`/ops/tenants/${id}/features`, { method: "PUT", json: { flags } });
  } catch (error) {
    outcome = `erro=${errorCode(error)}`;
  }
  redirect(`/ops/tenants/${id}?${outcome}`);
}

export async function setAccessMode(form: FormData): Promise<void> {
  const id = encodeURIComponent(field(form, "tenant_id"));
  const mode = field(form, "access_mode");
  let outcome = "ok=acesso";
  try {
    if (!(ACCESS_MODES as readonly string[]).includes(mode)) throw new ApiError(422, "validation_error", "");
    // PUT replaces the whole setting: start from the current value to keep its other fields.
    const context = await api<TenantPanelContext>(`/admin/tenants/${id}/context`);
    const current = context.settings.storefront ?? {};
    await api(`/ops/tenants/${id}/settings/storefront`, {
      method: "PUT",
      json: { value: { ...current, access_mode: mode } },
    });
  } catch (error) {
    outcome = `erro=${errorCode(error)}`;
  }
  redirect(`/ops/tenants/${id}?${outcome}`);
}
