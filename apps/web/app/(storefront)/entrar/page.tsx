import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { safeStorePath } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";

import { StoreShell } from "../_store/store-shell";

export const metadata: Metadata = { robots: { index: false, follow: false } };

const ERRORS: Record<string, string> = {
  cancelado: "O login foi cancelado.",
  login_invalido: "Não foi possível confirmar sua conta Google. Tente de novo.",
  email_nao_verificado: "Sua conta Google não tem o e-mail verificado.",
  google_indisponivel: "O Google não respondeu agora. Tente em instantes.",
  login_indisponivel: "O login desta loja não está disponível agora.",
  loja_indisponivel: "Esta loja não está disponível no momento.",
  conta_indisponivel: "Esta conta não pode entrar na loja.",
  sessao_expirada: "O login expirou. Entre de novo.",
  muitas_tentativas: "Muitas tentativas. Aguarde um minuto e tente de novo.",
};

interface Policies {
  terms: { version: number } | null;
  privacy: { version: number } | null;
}

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ erro?: string; next?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { erro, next } = await searchParams;
  const target = safeStorePath(next ?? "/loja");
  const enabled = Boolean(context.features.customer_login);
  // Versions shown here are the ones recorded as accepted when the sign-in completes.
  const policies = await storefrontApi<Policies>(context, "/policies", {}, { anonymous: true, fresh: true });
  const shown = policies.kind === "ok" ? policies.data : { terms: null, privacy: null };
  const start = new URLSearchParams({ next: target });
  if (shown.terms) start.set("tv", String(shown.terms.version));
  if (shown.privacy) start.set("pv", String(shown.privacy.version));
  return (
    <StoreShell context={context}>
      <h1>Entrar em {context.tenant.name}</h1>
      {erro ? <p role="alert">{ERRORS[erro] ?? "Não foi possível entrar."}</p> : null}
      {enabled ? (
        <>
          <p>Use sua conta Google para ver o catálogo e acompanhar seus pedidos.</p>
          <p>
            <a className="button" href={`/auth/google/start?${start.toString()}`}>
              Entrar com Google
            </a>
          </p>
          {shown.terms || shown.privacy ? (
            <p className="muted">
              Ao entrar, você aceita{" "}
              {shown.terms ? <Link href="/politicas/termos">os termos de uso</Link> : null}
              {shown.terms && shown.privacy ? " e " : null}
              {shown.privacy ? <Link href="/politicas/privacidade">a política de privacidade</Link> : null} desta
              loja.
            </p>
          ) : null}
        </>
      ) : (
        <p className="muted">O login de clientes ainda não está disponível nesta loja.</p>
      )}
    </StoreShell>
  );
}
