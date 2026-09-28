"use client";

import Link from "next/link";
import { useParams } from "next/navigation";

import styles from "../../../panel.module.css";

/**
 * Qualquer erro numa tela da loja para aqui.
 *
 * Sem este arquivo, um 403 da API virava a tela branca do Next ("Application error: a
 * server-side exception has occurred") — que não diz nada a quem está usando e manda o lojista
 * abrir chamado. O React apaga a mensagem do erro em produção e só deixa o `digest`, então o
 * texto aqui é genérico de propósito: o que resolve é a saída (voltar, tentar de novo) e o
 * código para casar com o log do servidor.
 */
export default function PanelError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  const params = useParams<{ tenantId: string }>();
  return (
    <section className={styles.card}>
      <h1>Esta tela não abriu</h1>
      <p className="muted">
        Pode ser uma parte da loja que a sua conta não acessa, ou uma falha nossa. As outras
        telas continuam funcionando.
      </p>
      <div className={styles.formActions}>
        <button type="button" className={styles.button} onClick={reset}>
          Tentar de novo
        </button>
        {params?.tenantId ? (
          <Link className={styles.buttonGhost} href={`/t/${params.tenantId}`}>
            Voltar para a loja
          </Link>
        ) : null}
      </div>
      {error.digest ? <p className={styles.hint}>Código: {error.digest}</p> : null}
    </section>
  );
}
