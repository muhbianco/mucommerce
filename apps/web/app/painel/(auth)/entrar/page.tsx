import type { Metadata } from "next";

import { login } from "../../actions";
import styles from "../../panel.module.css";

export const metadata: Metadata = { title: "Entrar · Painel MuhBianco" };

const ERRORS: Record<string, string> = {
  credenciais: "E-mail ou senha inválidos.",
  limite: "Muitas tentativas. Aguarde alguns minutos e tente de novo.",
};

export default async function PanelLogin({
  searchParams,
}: {
  searchParams: Promise<{ erro?: string; next?: string }>;
}) {
  const { erro, next } = await searchParams;
  return (
    <div className={styles.shell}>
      <div className={`${styles.card} ${styles.login}`}>
        <h1>Painel MuhBianco</h1>
        {erro ? <p className={styles.error}>{ERRORS[erro] ?? "Não foi possível entrar."}</p> : null}
        <form action={login} className={styles.form}>
          <input type="hidden" name="next" value={next ?? "/"} />
          <label>
            E-mail
            <input name="email" type="email" autoComplete="username" required />
          </label>
          <label>
            Senha
            <input name="password" type="password" autoComplete="current-password" required />
          </label>
          <button type="submit" className={styles.button}>
            Entrar
          </button>
        </form>
      </div>
    </div>
  );
}
