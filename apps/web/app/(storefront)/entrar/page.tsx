import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { safeStorePath } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";

import styles from "../_store/store.module.css";
import { StoreShell } from "../_store/store-shell";
import { Notice, PageHead } from "../_store/ui";
import { loginWithPassword } from "./actions";

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
  senha_errada: "E-mail ou senha incorretos.",
  link_invalido: "Este link expirou ou já foi usado. Entre com a sua senha ou peça outro.",
  dados_invalidos: "Confira o e-mail e a senha.",
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
      <PageHead title={`Entrar em ${context.tenant.name}`} />
      {erro ? <Notice kind="error">{ERRORS[erro] ?? "Não foi possível entrar."}</Notice> : null}
      {enabled ? (
        <>
          <p>Entre para comprar e acompanhar seus pedidos.</p>
          <p>
            <a className="button" href={`/auth/google/start?${start.toString()}`}>
              Entrar com Google
            </a>
          </p>
          <form action={loginWithPassword} className={styles.checkoutForm} aria-label="Entrar com e-mail">
            <input type="hidden" name="next" value={target} />
            <label className={styles.field}>
              E-mail
              <input name="email" type="email" autoComplete="email" required maxLength={254} />
            </label>
            <label className={styles.field}>
              Senha
              <input name="password" type="password" autoComplete="current-password" required maxLength={128} />
            </label>
            <button type="submit" className="button">
              Entrar com e-mail
            </button>
          </form>
          <p>
            <Link href={`/entrar/criar?next=${encodeURIComponent(target)}`}>Criar conta com e-mail</Link>
            {" · "}
            <Link href="/entrar/esqueci">Esqueci a senha</Link>
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
