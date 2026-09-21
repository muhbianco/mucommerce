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
