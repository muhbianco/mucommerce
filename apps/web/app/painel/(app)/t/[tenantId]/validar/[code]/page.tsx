import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

// Cinco níveis: esta página é `validar/[code]/`, um a mais que as outras do painel.
import styles from "../../../../../panel.module.css";
import local from "./validar.module.css";

/**
 * A tela de quem está na porta.
 *
 * Desenhada para ser lida de relance, com o celular numa mão e uma fila na frente: um bloco
 * grande que é verde ou vermelho, a palavra que decide, e só depois o detalhe. Nada de id de
 * pedido ou dado do comprador — quem confere entrada confere entrada, e o aparelho pode estar
 * passando de mão em mão a noite toda.
 *
 * A validação acontece **ao abrir**: o QR leva direto aqui, e pedir um segundo toque para
 * confirmar só adicionaria uma etapa na fila. O que protege contra queimar sem querer é a
 * idempotência do outro lado — a segunda leitura não "falha", ela informa quando a primeira
 * aconteceu.
 */

type Validation = {
  admitted: boolean;
  reason: string;
  code: string | null;
  event_name: string | null;
  lot_name: string | null;
  used_at: string | null;
};

const MOTIVO: Record<string, string> = {
  ok: "Pode entrar",
  used: "Esta entrada já foi registrada",
  refunded: "Pedido estornado: não vale",
  void: "Ingresso cancelado pela organização",
  not_found: "Ingresso não encontrado nesta loja",
};

export default async function ValidarPage({
  params,
}: {
  params: Promise<{ tenantId: string; code: string }>;
}) {
  const { tenantId, code } = await params;
  const [context, me] = await Promise.all([
    loadTenantContext(tenantId),
    requireMe(),
  ]);
  if (
    !context.features.events ||
    !tenantScopes(me, context.tenant_id).can("tickets:validate")
  ) {
    notFound();
  }

  const limpo = code.replace(/[^A-Za-z0-9-]/g, "").slice(0, 32);
  // API fora do ar não pode virar tela verde: sem resposta, a porta lê "não foi possível
  // conferir agora" e chama alguém, em vez de deixar entrar no escuro.
  let data: Validation | null = null;
  try {
    data = await api<Validation>(
      `/admin/tenants/${tenantId}/tickets/${encodeURIComponent(limpo)}/validate`,
      { method: "POST" },
    );
  } catch {
    data = null;
  }
  const admitido = data?.admitted === true;
  const motivo =
    MOTIVO[data?.reason ?? ""] ?? "Não foi possível conferir agora";

  return (
    <main className={styles.page}>
      <div className={admitido ? local.verdict : local.verdictDenied}>
        <p className={local.mark}>{admitido ? "✓" : "✕"}</p>
        <p className={local.word}>{motivo}</p>
      </div>

      {data?.event_name ? (
        <p className={local.detail}>
          <strong>{data.event_name}</strong>
          {data.lot_name ? ` · ${data.lot_name}` : ""}
        </p>
      ) : null}
      {data?.code ? <p className={local.code}>{data.code}</p> : null}
      {data?.used_at ? (
        <p className={local.detail}>
          Entrada registrada em{" "}
          {new Intl.DateTimeFormat("pt-BR", {
            dateStyle: "short",
            timeStyle: "short",
            timeZone: context.timezone,
          }).format(new Date(data.used_at))}
          .
        </p>
      ) : null}

      <p className={local.detail}>
        <Link href={`/t/${tenantId}/produtos`}>← Voltar ao painel</Link>
      </p>
    </main>
  );
}
