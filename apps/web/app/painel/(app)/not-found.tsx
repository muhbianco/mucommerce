import { headers } from "next/headers";

import { logout } from "../actions";
import styles from "../panel.module.css";

// Painel logado que não achou a loja: a API responde 404 tanto para loja inexistente quanto
// para loja em que a conta não tem vínculo (não revela quais lojas existem). Aqui a página
// fica dentro do painel, com a saída à mão; o 404 da raiz não tem como deslogar.
export default async function PanelNotFound() {
  const storeHost = (await headers()).get("x-host-kind") === "tenant_panel";
  return (
    <section className={styles.card}>
      <h1>{storeHost ? "Esta conta não tem acesso a esta loja" : "Loja não encontrada"}</h1>
      <p className="muted">
        {storeHost
          ? "Você entrou com uma conta que não faz parte da equipe desta loja. Saia e entre com a conta do dono da loja."
          : "Essa loja não existe ou a sua conta não tem acesso a ela."}
      </p>
      <form action={logout}>
        <button type="submit" className={styles.button}>
          Sair e entrar com outra conta
        </button>
      </form>
    </section>
  );
}
