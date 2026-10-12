"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE, safeStorePath } from "@/lib/customer-cookies";
import { destinationAfterSignIn, keepSession, type SignedIn } from "@/lib/customer-signin";
import { getStorefrontContext } from "@/lib/server-context";

/**
 * Conta com e-mail e senha (docs/14-pedidos-lunares.md §2). Tudo passa pela API com o token do
 * web; a resposta de cadastro e de "esqueci a senha" é sempre a mesma, exista a conta ou não.
 */

const MAX_PASSWORD = 128;

function field(form: FormData, name: string, max: number): string {
  return String(form.get(name) ?? "").slice(0, max);
}

/** O link do e-mail traz `id.token` num parâmetro só. */
function linkParts(form: FormData): { challenge_id: string; token: string } | null {
  const [challengeId, token] = field(form, "k", 120).split(".", 2);
  if (!challengeId || !token || challengeId.length !== 36) return null;
  return { challenge_id: challengeId, token };
}

function errorCode(error: unknown): string {
  if (!(error instanceof CustomerApiError)) throw error;
  if (error.status === 429) return "muitas_tentativas";
  if (error.code === "invalid_credentials") return "senha_errada";
  if (error.code === "weak_password") return "senha_fraca";
  if (error.code === "invalid_link") return "link_invalido";
  if (error.code === "customer_unavailable") return "conta_indisponivel";
  if (error.status === 422) return "dados_invalidos";
  return "login_indisponivel";
}

async function previousSession(): Promise<string | null> {
  return (await cookies()).get(CUSTOMER_SESSION_COOKIE)?.value ?? null;
}

async function signedIn(done: SignedIn): Promise<never> {
  await keepSession(done);
  redirect(destinationAfterSignIn(await getStorefrontContext(), done));
}

export async function loginWithPassword(form: FormData): Promise<void> {
  const next = safeStorePath(field(form, "next", 512) || "/loja");
  let done: SignedIn;
  try {
    done = await customerApi<SignedIn>("/internal/customer-auth/password/login", {
      json: {
        email: field(form, "email", 254).trim(),
        password: field(form, "password", MAX_PASSWORD),
        return_to: next,
        previous_session: await previousSession(),
      },
      session: null,
    });
  } catch (error) {
    redirect(`/entrar?${new URLSearchParams({ erro: errorCode(error), next }).toString()}`);
  }
  await signedIn(done);
}

export async function signupWithPassword(form: FormData): Promise<void> {
  const next = safeStorePath(field(form, "next", 512) || "/loja");
  const version = (name: string) => {
    const value = Number(form.get(name));
    return Number.isInteger(value) && value > 0 && value <= 100_000 ? value : undefined;
  };
  const back = (query: Record<string, string>) =>
    `/entrar/criar?${new URLSearchParams({ ...query, next }).toString()}`;
  if (field(form, "password", MAX_PASSWORD) !== field(form, "password_again", MAX_PASSWORD)) {
    redirect(back({ erro: "senhas_diferentes" }));
  }
  try {
    await customerApi("/internal/customer-auth/password/signup", {
      json: {
        email: field(form, "email", 254).trim(),
        name: field(form, "name", 200).trim(),
        password: field(form, "password", MAX_PASSWORD),
        return_to: next,
        terms_version: version("tv"),
        privacy_version: version("pv"),
      },
      session: null,
    });
  } catch (error) {
    redirect(back({ erro: errorCode(error) }));
  }
  redirect(back({ ok: "enviado" }));
}

export async function confirmSignup(form: FormData): Promise<void> {
  const parts = linkParts(form);
  if (!parts) redirect("/entrar?erro=link_invalido");
  let done: SignedIn;
  try {
    done = await customerApi<SignedIn>("/internal/customer-auth/password/confirm", {
      json: { ...parts, previous_session: await previousSession() },
      session: null,
    });
  } catch (error) {
    redirect(`/entrar?erro=${errorCode(error)}`);
  }
  await signedIn(done);
}

export async function forgotPassword(form: FormData): Promise<void> {
  try {
    await customerApi("/internal/customer-auth/password/forgot", {
      json: { email: field(form, "email", 254).trim() },
      session: null,
    });
  } catch (error) {
    redirect(`/entrar/esqueci?erro=${errorCode(error)}`);
  }
  redirect("/entrar/esqueci?ok=enviado");
}

export async function resetPassword(form: FormData): Promise<void> {
  const parts = linkParts(form);
  if (!parts) redirect("/entrar/esqueci?erro=link_invalido");
  const k = field(form, "k", 120);
  if (field(form, "password", MAX_PASSWORD) !== field(form, "password_again", MAX_PASSWORD)) {
    redirect(`/entrar/nova-senha?${new URLSearchParams({ k, erro: "senhas_diferentes" }).toString()}`);
  }
  let done: SignedIn;
  try {
    done = await customerApi<SignedIn>("/internal/customer-auth/password/reset", {
      json: { ...parts, password: field(form, "password", MAX_PASSWORD) },
      session: null,
    });
  } catch (error) {
    const code = errorCode(error);
    // Senha fraca não gasta o link: a pessoa tenta outra na mesma tela.
    const back = code === "senha_fraca" ? `/entrar/nova-senha?${new URLSearchParams({ k, erro: code })}` : `/entrar/esqueci?erro=${code}`;
    redirect(back);
  }
  await signedIn(done);
}
