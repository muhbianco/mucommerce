import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { safeStorePath } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";

import styles from "../../_store/store.module.css";
import { StoreShell } from "../../_store/store-shell";
import { Notice, PageHead } from "../../_store/ui";
import { signupWithPassword } from "../actions";

export const metadata: Metadata = { robots: { index: false, follow: false } };

const ERRORS: Record<string, string> = {
  senhas_diferentes: "As duas senhas não são iguais.",
  senha_fraca: "Essa senha é fácil de adivinhar. Use pelo menos 8 caracteres, misturando letras e números.",
  dados_invalidos: "Confira o nome e o e-mail.",
  muitas_tentativas: "Muitas tentativas. Aguarde alguns minutos e tente de novo.",
  login_indisponivel: "Não foi possível criar a conta agora. Tente em instantes.",
};

interface Policies {
  terms: { version: number } | null;
  privacy: { version: number } | null;
}

/** Cadastro com e-mail e senha. A conta só existe depois do clique no link do e-mail. */
export default async function SignupPage({
  searchParams,
}: {
  searchParams: Promise<{ erro?: string; ok?: string; next?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context || !context.features.customer_login) notFound();
  const { erro, ok, next } = await searchParams;
  const target = safeStorePath(next ?? "/loja");
  const policies = await storefrontApi<Policies>(context, "/policies", {}, { anonymous: true, fresh: true });
  const shown = policies.kind === "ok" ? policies.data : { terms: null, privacy: null };
  return (
    <StoreShell context={context}>
      <PageHead title={`Criar conta em ${context.tenant.name}`} />
      {ok === "enviado" ? (
        <>
          <Notice kind="ok">
            Pronto! Enviamos um link para o seu e-mail. Abra e confirme para entrar: ele vale por 24 horas.
          </Notice>
          <p className="muted">Não chegou? Confira o spam. Se você já tinha conta, o e-mail explica como entrar.</p>
        </>
      ) : (
        <>
          {erro ? <Notice kind="error">{ERRORS[erro] ?? "Não foi possível criar a conta."}</Notice> : null}
          <form action={signupWithPassword} className={styles.checkoutForm}>
            <input type="hidden" name="next" value={target} />
            {shown.terms ? <input type="hidden" name="tv" value={shown.terms.version} /> : null}
            {shown.privacy ? <input type="hidden" name="pv" value={shown.privacy.version} /> : null}
            <label className={styles.field}>
              Nome
              <input name="name" autoComplete="name" required maxLength={200} />
            </label>
            <label className={styles.field}>
              E-mail
              <input name="email" type="email" autoComplete="email" required maxLength={254} />
            </label>
            <label className={styles.field}>
              Senha
              <input name="password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} />
              <span className={styles.fieldHint}>Pelo menos 8 caracteres.</span>
            </label>
            <label className={styles.field}>
              Repita a senha
              <input
                name="password_again"
                type="password"
                autoComplete="new-password"
                required
                minLength={8}
                maxLength={128}
              />
            </label>
            {shown.terms || shown.privacy ? (
              <p className="muted">
                Ao criar a conta, você aceita{" "}
                {shown.terms ? <Link href="/politicas/termos">os termos de uso</Link> : null}
                {shown.terms && shown.privacy ? " e " : null}
                {shown.privacy ? <Link href="/politicas/privacidade">a política de privacidade</Link> : null} desta
                loja.
              </p>
            ) : null}
            <button type="submit" className="button">
              Criar conta
            </button>
          </form>
        </>
      )}
      <p>
        <Link href={`/entrar?next=${encodeURIComponent(target)}`}>Já tenho conta</Link>
      </p>
    </StoreShell>
  );
}
