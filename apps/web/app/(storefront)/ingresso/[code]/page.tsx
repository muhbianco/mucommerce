import type { Metadata } from "next";
import { notFound } from "next/navigation";
import { cache } from "react";

import { getStorefrontContext } from "@/lib/server-context";
import { storeOrigin } from "@/lib/storefront";
import { storefrontApi } from "@/lib/storefront-api";

import { StoreShell } from "../../_store/store-shell";
import styles from "../../_store/store.module.css";
import { qrSvg } from "./qr";

/**
 * O ingresso do cliente, com o QR que a porta vai ler.
 *
 * Mora no host da loja e dentro do `StoreShell` de propósito (ADR 0020, decisão 6): o ingresso
 * sai com a identidade do lojista, e um link para domínio nosso carregaria a nossa marca antes
 * de qualquer CSS. Quem compra ingresso do show da Lunares comprou da Lunares.
 *
 * Público por código: é o equivalente digital de um papel no bolso. Exigir login quebraria o
 * caso mais comum — o comprador repassa o ingresso para quem vai junto — e não protegeria nada,
 * porque o código é justamente o que o QR carrega. O que exige autenticação é **queimar** o
 * ingresso, não vê-lo.
 */

type Ticket = {
  code: string;
  status: "issued" | "used" | "refunded" | "void";
  event_name: string;
  lot_name: string;
  holder_name: string | null;
  used_at: string | null;
};

const loadTicket = cache(async (code: string) => {
  const context = await getStorefrontContext();
  if (!context) return { kind: "not_found" as const };
  return storefrontApi<Ticket>(context, `/tickets/${encodeURIComponent(code.slice(0, 32))}`);
});

export async function generateMetadata({
  params,
}: {
  params: Promise<{ code: string }>;
}): Promise<Metadata> {
  const { code } = await params;
  const result = await loadTicket(code);
  if (result.kind !== "ok") return {};
  return {
    title: `Ingresso · ${result.data.event_name}`,
    // Ingresso não é página de catálogo: ninguém ganha nada com ele indexado, e o código no
    // endereço é o que dá acesso.
    robots: { index: false, follow: false },
  };
}

const ESTADO: Record<Ticket["status"], { rotulo: string; recado: string }> = {
  issued: { rotulo: "Válido", recado: "Mostre este código na entrada." },
  used: { rotulo: "Já utilizado", recado: "Esta entrada já foi registrada." },
  refunded: { rotulo: "Estornado", recado: "O pedido foi estornado: este ingresso não vale." },
  void: { rotulo: "Cancelado", recado: "Este ingresso foi cancelado pela organização." },
};

export default async function TicketPage({ params }: { params: Promise<{ code: string }> }) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { code } = await params;
  const result = await loadTicket(code);
  if (result.kind !== "ok") notFound();
  const ticket = result.data;
  const estado = ESTADO[ticket.status] ?? ESTADO.void;
  const valido = ticket.status === "issued";
  // O QR carrega o endereço de validação, não o código solto: quem está na porta escaneia e já
  // cai na tela que queima o ingresso, sem digitar nada.
  const validacao = `${storeOrigin(context)}/validar/${ticket.code}`;

  return (
    <StoreShell context={context}>
      <article className={styles.ticket}>
        <p className={styles.ticketEvent}>{ticket.event_name}</p>
        <p className={styles.ticketLot}>{ticket.lot_name}</p>
        {ticket.holder_name ? <p className={styles.ticketHolder}>{ticket.holder_name}</p> : null}

        <div
          className={valido ? styles.ticketQr : styles.ticketQrOff}
          // O SVG é gerado aqui no servidor, a partir do nosso próprio endereço: não há
          // conteúdo de terceiro nem entrada de usuário dentro dele.
          dangerouslySetInnerHTML={{ __html: qrSvg(validacao) }}
          role="img"
          aria-label={`QR code do ingresso ${ticket.code}`}
        />

        <p className={styles.ticketCode}>{ticket.code}</p>
        <p className={valido ? styles.tag : styles.soldOut}>{estado.rotulo}</p>
        <p className={styles.ticketHint}>{estado.recado}</p>
        {ticket.used_at ? (
          <p className={styles.ticketHint}>
            Entrada registrada em{" "}
            {new Intl.DateTimeFormat("pt-BR", {
              dateStyle: "short",
              timeStyle: "short",
              timeZone: context.tenant.timezone,
            }).format(new Date(ticket.used_at))}
            .
          </p>
        ) : null}
      </article>
    </StoreShell>
  );
}
