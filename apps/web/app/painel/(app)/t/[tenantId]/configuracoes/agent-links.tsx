"use client";

import { useRouter } from "next/navigation";
import { useState, useTransition } from "react";

import styles from "../../../../panel.module.css";
import { createAgentLinkCode, revokeAgentLink } from "../actions";
import local from "./configuracoes.module.css";

export interface AgentLink {
  id: string;
  tipo: string;
  nome: string | null;
  conta: string | null;
  criado_em: string;
  ultimo_uso: string | null;
  revogado_em: string | null;
  permissoes: string[];
}

/** O que cada tipo libera, em palavras de lojista — não em nomes de escopo. */
const TIPOS: { value: "sales" | "operator"; label: string; resumo: string }[] = [
  {
    value: "sales",
    label: "Atende e vende",
    resumo: "Vê o catálogo e registra pedidos. Não mexe em estoque, preço nem na vitrine.",
  },
  {
    value: "operator",
    label: "Cuida da loja",
    resumo: "Faz o que você faz no painel: estoque, vitrine e andamento dos pedidos.",
  },
];

const TIPO_LABEL: Record<string, string> = {
  sales: "Atende e vende",
  operator: "Cuida da loja",
};

function quando(iso: string | null, timezone: string): string {
  if (!iso) return "nunca";
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "short",
    timeZone: timezone,
  }).format(new Date(iso));
}

/**
 * Conectar um agente a esta loja.
 *
 * O código aparece **uma vez**, aqui na tela, e não passa pela URL: código de acesso no
 * histórico do navegador é código de acesso guardado onde ninguém controla. Quem recebe tem
 * meia hora para usá-lo, e ele morre no primeiro uso.
 */
export function AgentLinks({
  tenantId,
  links,
  timezone,
}: {
  tenantId: string;
  links: AgentLink[];
  timezone: string;
}) {
  const router = useRouter();
  const [pendente, startTransition] = useTransition();
  const [tipo, setTipo] = useState<"sales" | "operator">("sales");
  const [nome, setNome] = useState("");
  const [codigo, setCodigo] = useState<string | null>(null);
  const [erro, setErro] = useState<string | null>(null);

  const gerar = () => {
    setErro(null);
    setCodigo(null);
    startTransition(async () => {
      const resposta = await createAgentLinkCode(tenantId, tipo, nome.trim());
      if (resposta.ok) {
        setCodigo(resposta.codigo);
        setNome("");
      } else {
        setErro(resposta.erro);
      }
    });
  };

  const desconectar = (link: AgentLink) => {
    setErro(null);
    startTransition(async () => {
      const resposta = await revokeAgentLink(tenantId, link.id);
      if (resposta.ok) router.refresh();
      else setErro(resposta.erro ?? "Não foi possível desconectar.");
    });
  };

  const escolhido = TIPOS.find((t) => t.value === tipo);
  const conectados = links.filter((link) => !link.revogado_em);

  return (
    <>
      <p className={styles.hint}>
        Um agente conectado atende pelo WhatsApp usando o catálogo e os pedidos desta loja. Você
        gera o código, passa para quem vai configurá-lo, e pode desconectar quando quiser — sem
        mexer na senha de ninguém.
      </p>

      <div className={styles.fields}>
        <label className={styles.field}>
          O que ele pode fazer
          <select value={tipo} onChange={(e) => setTipo(e.target.value as "sales" | "operator")}>
            {TIPOS.map((opcao) => (
              <option key={opcao.value} value={opcao.value}>
                {opcao.label}
              </option>
            ))}
          </select>
          <span className={styles.fieldHint}>{escolhido?.resumo}</span>
        </label>
        <label className={styles.field}>
          Nome (para você reconhecer depois)
          <input
            value={nome}
            onChange={(e) => setNome(e.target.value)}
            maxLength={80}
            placeholder="ex.: atendimento da loja"
          />
        </label>
      </div>

      <div className={styles.formActions}>
        <button type="button" className={styles.button} onClick={gerar} disabled={pendente}>
          {pendente ? "Gerando…" : "Gerar código"}
        </button>
      </div>

      {codigo ? (
        <div className={local.linkCode}>
          <p className={styles.hint}>
            Passe este código para quem vai configurar o agente. Ele vale por 30 minutos e serve
            uma vez só — <strong>não aparece de novo</strong>, mas gerar outro é de graça.
          </p>
          <strong className={local.linkCodeValue}>{codigo}</strong>
        </div>
      ) : null}
      {erro ? <p className={styles.fieldHint}>{erro}</p> : null}

      {conectados.length ? (
        <ul className={local.linkList}>
          {conectados.map((link) => (
            <li key={link.id} className={local.linkRow}>
              <span>
                <strong>{link.nome || TIPO_LABEL[link.tipo] || link.tipo}</strong>
                <span className={styles.fieldHint}>
                  {TIPO_LABEL[link.tipo] ?? link.tipo} · conectado em{" "}
                  {quando(link.criado_em, timezone)} · último uso{" "}
                  {quando(link.ultimo_uso, timezone)}
                </span>
              </span>
              <button
                type="button"
                className={styles.buttonGhost}
                onClick={() => desconectar(link)}
                disabled={pendente}
              >
                Desconectar
              </button>
            </li>
          ))}
        </ul>
      ) : (
        <p className={styles.hint}>Nenhum agente conectado a esta loja.</p>
      )}
    </>
  );
}
