import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";

import styles from "../../_store/store.module.css";
import { StoreShell } from "../../_store/store-shell";
import { Notice, PageHead } from "../../_store/ui";
import { resetPassword } from "../actions";

export const metadata: Metadata = {
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

const ERRORS: Record<string, string> = {
  senhas_diferentes: "As duas senhas não são iguais.",
  senha_fraca: "Essa senha é fácil de adivinhar. Use pelo menos 8 caracteres, misturando letras e números.",
};

export default async function NewPasswordPage({
  searchParams,
}: {
  searchParams: Promise<{ k?: string; erro?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { k, erro } = await searchParams;
  return (
    <StoreShell context={context}>
      <PageHead title="Escolha uma senha nova" />
      {erro ? <Notice kind="error">{ERRORS[erro] ?? "Não foi possível trocar a senha."}</Notice> : null}
      {k ? (
        <form action={resetPassword} className={styles.checkoutForm}>
          <input type="hidden" name="k" value={k.slice(0, 120)} />
          <label className={styles.field}>
            Senha nova
            <input name="password" type="password" autoComplete="new-password" required minLength={8} maxLength={128} />
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
          <p className="muted">Ao trocar, as sessões abertas em outros aparelhos são encerradas.</p>
          <button type="submit" className="button">
            Trocar e entrar
          </button>
        </form>
      ) : (
        <p>Este link está incompleto. Abra de novo o link do e-mail.</p>
      )}
    </StoreShell>
  );
}
