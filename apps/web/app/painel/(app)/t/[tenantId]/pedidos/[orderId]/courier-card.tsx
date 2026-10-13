import Link from "next/link";

import { formatMoney, parseInstant } from "@/lib/panel/format";
import type { PillState } from "@/lib/panel/states";
import type { CourierDelivery, CourierRead } from "@/lib/panel/types";

import styles from "../../../../../panel.module.css";
import { KeyValues, Pill, Section } from "../../ui";
import { courierAction } from "../actions";
import local from "./order.module.css";

/** O que a Uber disse (`provider_status`), em palavras de lojista. */
const UBER_STATUS: Record<string, string> = {
  pending: "Procurando entregador",
  pickup: "Entregador a caminho da loja",
  pickup_complete: "Pedido coletado",
  dropoff: "A caminho do cliente",
};

function situacao(row: CourierDelivery): { label: string; state: PillState } {
  switch (row.status) {
    case "requested":
    case "creating":
      return { label: "Chamando a Uber…", state: "pending" };
    case "awaiting_confirmation":
      return { label: "Cotação acima do teto", state: "warn" };
    case "active":
      return { label: UBER_STATUS[row.provider_status ?? ""] ?? "Na Uber", state: "live" };
    case "cancelling":
      return { label: "Cancelando na Uber…", state: "pending" };
    case "delivered":
      return { label: "Entregue", state: "live" };
    case "failed":
      return { label: "Não deu", state: "warn" };
    default:
      return { label: "Cancelada", state: "off" };
  }
}

const FALTA: Record<CourierRead["missing"][number], string> = {
  pronto: 'Marque o pedido como "Pronto para o entregador" para chamar a Uber.',
  chaves: "Conecte a conta da Uber em Entrega e checkout.",
  coleta: "Informe o endereço de coleta da Uber em Entrega e checkout.",
};

/**
 * A corrida da Uber do pedido de entrega (docs/15). O cliente pagou a taxa da zona; o que a Uber
 * cobra pela corrida sai da conta Uber da loja e aparece aqui como "Custo da corrida".
 */
export function CourierCard({
  tenantId,
  orderId,
  courier,
  canMove,
  timezone,
}: {
  tenantId: string;
  orderId: string;
  courier: CourierRead;
  canMove: boolean;
  timezone: string;
}) {
  const when = (iso: string | null) =>
    parseInstant(iso)?.toLocaleString("pt-BR", { timeZone: timezone, dateStyle: "short", timeStyle: "short" }) ??
    "—";
  const aberta = courier.open;
  const anterior = courier.history[0];
  const botao = (acao: string, rotulo: string, classe: string | undefined) => (
    <form action={courierAction}>
      <input type="hidden" name="tenant_id" value={tenantId} />
      <input type="hidden" name="order_id" value={orderId} />
      <input type="hidden" name="courier_action" value={acao} />
      <button type="submit" className={classe}>
        {rotulo}
      </button>
    </form>
  );
  const ghost = `${styles.buttonGhost} ${styles.buttonSmall}`;
  const atual = aberta ? situacao(aberta) : null;

  return (
    <Section
      title="Entrega pela Uber"
      description={courier.uber_zone ? "A zona deste pedido é entregue pela Uber" : "Chamada avulsa pela loja"}
      actions={
        <>
          {atual ? <Pill state={atual.state}>{atual.label}</Pill> : null}
          {aberta?.environment === "test" ? <Pill state="info">Teste</Pill> : null}
        </>
      }
    >
      {aberta ? (
        <>
          <KeyValues
            items={[
              ...(aberta.fee_cents !== null
                ? [{ label: "Custo da corrida", value: `${formatMoney(aberta.fee_cents)} (na sua conta Uber)` }]
                : []),
              ...(aberta.status === "awaiting_confirmation"
                ? [
                    { label: "Cotação da Uber", value: formatMoney(aberta.quoted_fee_cents) },
                    { label: "Seu teto", value: formatMoney(aberta.max_fee_cents) },
                  ]
                : []),
              ...(aberta.courier_name
                ? [
                    {
                      label: "Entregador",
                      value: [aberta.courier_name, aberta.courier_vehicle].filter(Boolean).join(" · "),
                    },
                  ]
                : []),
              ...(aberta.pickup_eta && !aberta.picked_up_at
                ? [{ label: "Coleta prevista", value: when(aberta.pickup_eta) }]
                : []),
              ...(aberta.dropoff_eta ? [{ label: "Entrega prevista", value: when(aberta.dropoff_eta) }] : []),
              ...(aberta.tracking_url
                ? [
                    {
                      label: "Acompanhar",
                      value: (
                        <Link href={aberta.tracking_url} target="_blank" rel="noopener noreferrer">
                          Abrir na Uber
                        </Link>
                      ),
                    },
                  ]
                : []),
            ]}
          />
          {aberta.last_error && aberta.status !== "awaiting_confirmation" ? (
            <p className={styles.note}>{aberta.last_error}</p>
          ) : null}
          {canMove ? (
            <div className={local.actions}>
              {aberta.status === "awaiting_confirmation"
                ? botao("confirm", `Chamar mesmo assim (${formatMoney(aberta.quoted_fee_cents)})`, styles.button)
                : null}
              {aberta.status !== "awaiting_confirmation" ? botao("refresh", "Atualizar", ghost) : null}
              {aberta.status !== "cancelling" ? botao("own", "Levar eu mesmo", ghost) : null}
              {aberta.status !== "cancelling" ? botao("cancel", "Cancelar corrida", `${styles.buttonDanger} ${styles.buttonSmall}`) : null}
            </div>
          ) : null}
        </>
      ) : (
        <>
          {anterior ? (
            <p className={styles.hint}>
              Última corrida: <strong>{situacao(anterior).label}</strong>
              {anterior.last_error ? ` — ${anterior.last_error}` : ""}.
            </p>
          ) : null}
          {courier.missing.length ? (
            <ul className={styles.hint}>
              {courier.missing.map((item) => (
                <li key={item}>
                  {item === "pronto" ? (
                    FALTA[item]
                  ) : (
                    <Link href={`/t/${tenantId}/entrega#uber`}>{FALTA[item]}</Link>
                  )}
                </li>
              ))}
            </ul>
          ) : canMove ? (
            <div className={local.actions}>
              {botao("request", anterior ? "Chamar a Uber de novo" : "Chamar a Uber", styles.button)}
            </div>
          ) : (
            <p className={styles.hint}>Seu papel não permite chamar a Uber.</p>
          )}
        </>
      )}
      {courier.history.length > (aberta ? 0 : 1) ? (
        <details className={local.courierHistory}>
          <summary>Tentativas anteriores</summary>
          <ul>
            {courier.history.slice(aberta ? 0 : 1).map((row) => (
              <li key={row.id}>
                {when(row.created_at)} — {situacao(row).label}
                {row.last_error ? ` (${row.last_error})` : ""}
              </li>
            ))}
          </ul>
        </details>
      ) : null}
    </Section>
  );
}
