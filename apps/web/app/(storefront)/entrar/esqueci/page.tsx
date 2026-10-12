import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";

import styles from "../../_store/store.module.css";
import { StoreShell } from "../../_store/store-shell";
import { Notice, PageHead } from "../../_store/ui";
import { forgotPassword } from "../actions";

export const metadata: Metadata = { robots: { index: false, follow: false } };

const ERRORS: Record<string, string> = {
  link_invalido: "Este link expirou ou já foi usado. Peça outro abaixo.",
  muitas_tentativas: "Muitas tentativas. Aguarde alguns minutos e tente de novo.",
  dados_invalidos: "Confira o e-mail.",
};

export default async function ForgotPage({ searchParams }: { searchParams: Promise<{ erro?: string; ok?: string }> }) {
  const context = await getStorefrontContext();
  if (!context || !context.features.customer_login) notFound();
  const { erro, ok } = await searchParams;
  return (
    <StoreShell context={context}>
      <PageHead title="Trocar a senha" />
      {ok === "enviado" ? (
        <Notice kind="ok">
          Se este e-mail tem conta com senha nesta loja, enviamos um link para trocar. Ele vale por 1 hora.
        </Notice>
      ) : (
        <>
          {erro ? <Notice kind="error">{ERRORS[erro] ?? "Não foi possível enviar agora."}</Notice> : null}
          <form action={forgotPassword} className={styles.checkoutForm}>
            <label className={styles.field}>
              E-mail da conta
              <input name="email" type="email" autoComplete="email" required maxLength={254} />
            </label>
            <button type="submit" className="button">
              Enviar link
            </button>
          </form>
          <p className="muted">Entra com Google? Então não há senha para trocar: use o botão do Google.</p>
        </>
      )}
      <p>
        <Link href="/entrar">Voltar para entrar</Link>
      </p>
    </StoreShell>
  );
}
