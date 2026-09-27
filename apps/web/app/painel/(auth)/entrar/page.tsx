import type { Metadata } from "next";

import { login } from "../../actions";
import { panelFonts } from "../../fonts";
import styles from "../../panel.module.css";

export const metadata: Metadata = { title: "Entrar · Painel MuhBianco" };

const ERRORS: Record<string, string> = {
  sso: "Não foi possível entrar com a conta MuhBianco. Tente de novo.",
  sso_indisponivel: "O login MuhBianco está indisponível no momento. Tente em instantes.",
  credenciais: "E-mail ou senha inválidos.",
  limite: "Muitas tentativas. Aguarde alguns minutos e tente de novo.",
};

export default async function PanelLogin({
  searchParams,
}: {
  searchParams: Promise<{ erro?: string; next?: string }>;
}) {
  const { erro, next } = await searchParams;
  const start = `/sso/start?next=${encodeURIComponent(next ?? "/")}`;
  return (
    <div className={`${styles.shell} ${panelFonts}`}>
      <div className={`${styles.card} ${styles.login}`}>
        <span className={styles.eyebrow}>Painel MuhBianco</span>
        <h1>Entrar no painel</h1>
        {erro ? <p className={styles.error}>{ERRORS[erro] ?? "Não foi possível entrar."}</p> : null}
        <p className={styles.lead}>Use a mesma conta Google da sua conta MuhBianco.</p>
        <a href={start} className={`${styles.button} ${styles.loginCta}`}>
          Entrar com Google
        </a>
        <details style={{ marginTop: "1.5rem" }}>
          <summary className="muted">Acesso de emergência</summary>
          <form action={login} className={styles.form} style={{ marginTop: "0.75rem" }}>
            <input type="hidden" name="next" value={next ?? "/"} />
            <label>
              E-mail
              <input name="email" type="email" autoComplete="username" required />
            </label>
            <label>
              Senha
              <input name="password" type="password" autoComplete="current-password" required />
            </label>
            <button type="submit" className={styles.buttonGhost}>
              Entrar com senha
            </button>
          </form>
        </details>
      </div>
    </div>
  );
}
