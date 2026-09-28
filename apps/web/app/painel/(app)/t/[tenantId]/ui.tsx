/**
 * Kit do painel da loja. Toda página monta com estes blocos, para o painel inteiro ter a mesma
 * cara e a mesma ordem de leitura: cabeçalho da página → seções → ações.
 * Referência de uso: `dominios/page.tsx`.
 */
import Link from "next/link";
import type { ReactNode } from "react";

import type { PillState } from "@/lib/panel/states";

import styles from "../../../panel.module.css";

/** Título da página: rótulo pequeno (a seção do menu), título, frase que explica, ações. */
export function PageHeader({
  eyebrow,
  title,
  lead,
  actions,
}: {
  eyebrow: string;
  title: string;
  lead?: ReactNode;
  actions?: ReactNode;
}) {
  return (
    <header className={styles.pageHead}>
      <div className={styles.pageHeadRow}>
        <div>
          <span className={styles.eyebrow}>{eyebrow}</span>
          <h2>{title}</h2>
        </div>
        {actions ? <div className={styles.pageActions}>{actions}</div> : null}
      </div>
      {lead ? <p className={styles.lead}>{lead}</p> : null}
    </header>
  );
}

/** Bloco da página: cartão com título, descrição curta à direita e conteúdo. */
export function Section({
  title,
  description,
  actions,
  children,
  id,
}: {
  title: string;
  description?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  id?: string;
}) {
  return (
    <section className={styles.card} id={id}>
      <div className={styles.sectionHead}>
        <h3>{title}</h3>
        {description ? <p>{description}</p> : null}
        {actions ? <div className={styles.sectionActions}>{actions}</div> : null}
      </div>
      {children}
    </section>
  );
}

/** Selo de situação. `state` diz a cor; o texto diz a situação em palavras de lojista. */
export function Pill({ state, children }: { state: PillState; children: ReactNode }) {
  return (
    <span className={styles.pill} data-state={state}>
      {children}
    </span>
  );
}

/** Lista vazia: diz o que falta e o próximo passo, em vez de uma tabela sem linhas. */
export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className={styles.empty}>
      <p className={styles.emptyTitle}>{title}</p>
      {children ? <p className={styles.emptyText}>{children}</p> : null}
      {action ? <div className={styles.emptyAction}>{action}</div> : null}
    </div>
  );
}

/** Números de resumo em cartões (visão geral, estoque, clientes). */
export function Stats({ children }: { children: ReactNode }) {
  return <div className={styles.stats}>{children}</div>;
}

export function Stat({ label, value, hint, href }: { label: string; value: ReactNode; hint?: ReactNode; href?: string }) {
  const body = (
    <>
      <span className={styles.statLabel}>{label}</span>
      <span className={styles.statValue}>{value}</span>
      {hint ? <span className={styles.statHint}>{hint}</span> : null}
    </>
  );
  return href ? (
    <Link href={href} className={`${styles.stat} ${styles.statLink}`}>
      {body}
    </Link>
  ) : (
    <div className={styles.stat}>{body}</div>
  );
}

/** Pares rótulo → valor (detalhe de pedido, cliente, configurações só de leitura). */
export function KeyValues({ items }: { items: { label: string; value: ReactNode }[] }) {
  return (
    <dl className={styles.kv}>
      {items.map((item) => (
        <div key={item.label} className={styles.kvRow}>
          <dt>{item.label}</dt>
          <dd>{item.value}</dd>
        </div>
      ))}
    </dl>
  );
}

/** Tabela com rolagem própria no celular (a página nunca estoura a largura). */
export function TableWrap({ children }: { children: ReactNode }) {
  return <div className={styles.tableWrap}>{children}</div>;
}
