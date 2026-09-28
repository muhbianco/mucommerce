import type { Metadata } from "next";
import { notFound } from "next/navigation";
import type { ReactNode } from "react";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import type { PillState } from "@/lib/panel/states";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import { PAYMENT_METHOD_LABEL, PAYMENT_PROVIDERS, type PaymentProviderRead } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { CopyButton } from "../copy-button";
import { Flash } from "../flash";
import { EmptyState, KeyValues, PageHeader, Pill, Section } from "../ui";
import { savePaymentProvider, saveSurcharge, testPaymentProvider } from "./actions";
import local from "./pagamentos.module.css";

export const metadata: Metadata = { title: "Pagamentos" };

const MISSING_LABEL: Record<string, string> = {
  "secret:access_token": "access token",
  "secret:webhook_secret": "assinatura secreta dos webhooks",
  "public:public_key": "public key",
  "public:handle": "InfiniteTag",
};

/** Situação do meio em palavras de lojista: liberado? falta dado? está vendendo? */
function providerStatus(p: PaymentProviderRead): { label: string; state: PillState } {
  if (!p.flag_on) return { label: "Não liberado", state: "off" };
  if (p.missing.length) return { label: "Falta configurar", state: p.enabled ? "warn" : "pending" };
  if (p.enabled) return { label: "Ativo na loja", state: "live" };
  return { label: "Configurado, desligado", state: "off" };
}

export default async function Payments({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  const scopes = tenantScopes(me, context.tenant_id);
  if (!context.features.checkout || !scopes.can("payments:read")) notFound();
  // Who sets the token decides where the money goes: only the store's own owner edits here
  // (platform staff see the status, never the form — the API refuses them as well).
  const member = me.memberships.some((m) => m.tenant_id === context.tenant_id);
  const canConfigure = member && scopes.can("payments:config");
  interface Surcharge {
    enabled?: boolean;
    surcharge?: Record<string, { percent_bps?: number; fixed_cents?: number }>;
    card_installments?: { up_to: number; percent_bps: number }[];
  }
  const surcharge = (context.settings.payments ?? {}) as Surcharge;
  const card = surcharge.surcharge?.card ?? {};
  const pix = surcharge.surcharge?.pix ?? {};
  const tier = (upTo: number): number =>
    surcharge.card_installments?.find((t) => t.up_to === upTo)?.percent_bps ?? 0;
  const percent = (bps?: number): string => (bps ? String(bps / 100).replace(".", ",") : "");
  const reais = (cents?: number): string => (cents ? (cents / 100).toFixed(2).replace(".", ",") : "");

  const providers = await api<PaymentProviderRead[]>(`/admin/tenants/${context.tenant_id}/payments/providers`);
  const dateTime = (iso: string) =>
    new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: context.timezone }).format(
      new Date(iso),
    );
  const tenantField = <input type="hidden" name="tenant_id" value={context.tenant_id} />;
  const known = providers.filter((p) => PAYMENT_PROVIDERS[p.provider]);

  return (
    <>
      <PageHeader
        eyebrow="Pagamentos"
        title="Como os clientes pagam"
        lead="O dinheiro das vendas cai direto na conta da loja no meio de pagamento escolhido. As chaves ficam guardadas cifradas e nunca aparecem de novo nesta tela."
      />
      <Flash ok={ok} erro={erro} />

      <Section
        title="Repasse da taxa ao cliente"
        description="A lei permite preço diferente por meio de pagamento, desde que o cliente saiba antes de escolher — e é assim que aparece no checkout."
      >
        <form action={saveSurcharge}>
          <input type="hidden" name="tenant_id" value={tenantId} />
          <label className={styles.check}>
            <input type="checkbox" name="surcharge_enabled" defaultChecked={surcharge.enabled ?? false} />
            <span>
              Repassar a taxa para quem compra
              <span className={styles.fieldHint}>
                Desligado, a loja absorve a taxa e o preço é o mesmo em qualquer meio.
              </span>
            </span>
          </label>
          <div className={styles.fields}>
            <label className={styles.field}>
              Cartão — taxa (%)
              <input name="card_percent" inputMode="decimal" defaultValue={percent(card.percent_bps)} placeholder="3,5" />
            </label>
            <label className={styles.field}>
              Cartão — valor fixo (R$)
              <input name="card_fixed" inputMode="decimal" defaultValue={reais(card.fixed_cents)} placeholder="0,49" />
            </label>
            <label className={styles.field}>
              Pix — taxa (%)
              <input name="pix_percent" inputMode="decimal" defaultValue={percent(pix.percent_bps)} placeholder="0" />
            </label>
            <label className={styles.field}>
              Pix — valor fixo (R$)
              <input name="pix_fixed" inputMode="decimal" defaultValue={reais(pix.fixed_cents)} placeholder="0,00" />
            </label>
            <label className={styles.field}>
              Cartão à vista (%)
              <input name="card_1" inputMode="decimal" defaultValue={percent(tier(1))} placeholder="3,5" />
            </label>
            <label className={styles.field}>
              Cartão até 6x (%)
              <input name="card_6" inputMode="decimal" defaultValue={percent(tier(6))} placeholder="9" />
            </label>
            <label className={styles.field}>
              Cartão até 12x (%)
              <input name="card_12" inputMode="decimal" defaultValue={percent(tier(12))} placeholder="15" />
              <span className={styles.fieldHint}>
                Deixe as três vazias para cobrar a mesma taxa em qualquer parcelamento.
              </span>
            </label>
          </div>
          <div className={styles.formActions}>
            <button type="submit" className={styles.button}>
              Salvar repasse
            </button>
          </div>
        </form>
      </Section>

      {!canConfigure && known.length ? (
        <p className={styles.note}>Só o dono da loja altera os meios de pagamento. Aqui você vê como cada um está.</p>
      ) : null}

      {known.length === 0 ? (
        <EmptyState title="Nenhum meio de pagamento disponível">
          Fale com o suporte para liberar um meio de pagamento para a sua loja.
        </EmptyState>
      ) : null}

      {known.map((p) => {
        const spec = PAYMENT_PROVIDERS[p.provider];
        if (!spec) return null;
        const methods = p.methods ?? spec.methods;
        const status = providerStatus(p);
        const canTest = Object.keys(p.secrets).length > 0 || !spec.secrets.length;

        const facts: { label: string; value: ReactNode }[] = [];
        if (spec.secrets.length) {
          facts.push({
            label: "Endereço de notificações",
            value: (
              <>
                <span className={styles.copyValue}>
                  <code>{p.webhook_url}</code>
                  <CopyButton value={p.webhook_url} />
                </span>
                <span className={styles.fieldHint}>Cadastre este endereço no painel do {spec.label}.</span>
              </>
            ),
          });
          facts.push({
            label: "Último aviso recebido",
            value: p.last_webhook_at ? dateTime(p.last_webhook_at) : "Nenhum aviso recebido ainda",
          });
        }
        if (p.last_test_at) {
          facts.push({
            label: "Último teste",
            value: p.last_test_ok ? (
              <>
                <Pill state="live">Credenciais funcionando</Pill> em {dateTime(p.last_test_at)}
              </>
            ) : (
              <>
                <Pill state="warn">Falhou</Pill> em {dateTime(p.last_test_at)}: {p.last_test_error ?? "erro"}.
              </>
            ),
          });
        }

        const configForm = (
          <form action={savePaymentProvider}>
            {tenantField}
            <input type="hidden" name="provider" value={p.provider} />
            <div className={styles.fields}>
              {spec.public.map(({ key, label }) => (
                <label key={key} className={styles.field}>
                  {label}
                  <input
                    name={`public_${key}`}
                    maxLength={200}
                    defaultValue={p.public_config[key] ?? ""}
                    autoComplete="off"
                  />
                </label>
              ))}
              {spec.secrets.map(({ key, label }) => (
                <label key={key} className={styles.field}>
                  {label}
                  <input
                    type="password"
                    name={`secret_${key}`}
                    maxLength={500}
                    autoComplete="off"
                    placeholder={p.secrets[key] ? `guardado (${p.secrets[key].masked}) — em branco mantém` : ""}
                  />
                  <span className={styles.fieldHint}>
                    {p.secrets[key]
                      ? "Deixe em branco para manter o que já está guardado."
                      : `Copie do painel do ${spec.label}.`}
                  </span>
                </label>
              ))}
              {spec.methods.includes("card") ? (
                <label className={styles.field}>
                  Parcelas no cartão (até)
                  <input type="number" name="installments_max" min={1} max={12} defaultValue={p.installments_max} />
                  <span className={styles.fieldHint}>De 1 a 12.</span>
                </label>
              ) : null}
              <fieldset className={`${styles.fieldWide} ${local.group}`}>
                <legend>Meios oferecidos</legend>
                {spec.methods.map((m) => (
                  <label key={m} className={styles.check}>
                    <input type="checkbox" name={`method_${m}`} defaultChecked={methods.includes(m)} />{" "}
                    {PAYMENT_METHOD_LABEL[m] ?? m}
                  </label>
                ))}
              </fieldset>
              <fieldset className={`${styles.fieldWide} ${local.group}`}>
                <legend>Na loja</legend>
                <label className={styles.check}>
                  <input type="checkbox" name="enabled" defaultChecked={p.enabled} /> Ativo na loja
                </label>
                <label className={styles.check}>
                  <input type="checkbox" name="is_default" defaultChecked={p.is_default} /> Meio padrão
                </label>
                {spec.secrets.length ? (
                  <label className={styles.check}>
                    <input type="checkbox" name="sandbox" defaultChecked={p.sandbox} /> Credenciais de teste (sandbox)
                  </label>
                ) : null}
              </fieldset>
            </div>
            <div className={styles.formActions}>
              <button type="submit" className={styles.button}>
                Salvar {spec.label}
              </button>
            </div>
          </form>
        );

        return (
          <Section
            key={p.provider}
            title={spec.label}
            description={
              <span className={local.badges}>
                <Pill state={status.state}>{status.label}</Pill>
                {p.enabled && p.sandbox && spec.secrets.length ? <Pill state="warn">Em modo de teste</Pill> : null}
                {p.is_default ? <span className={styles.tag}>Padrão</span> : null}
              </span>
            }
          >
            {spec.hint ? <p className={styles.lead}>{spec.hint}</p> : null}
            {!p.flag_on ? (
              <p className={styles.hint}>Este meio não está liberado para a loja. Fale com o suporte para liberar.</p>
            ) : null}
            {p.missing.length ? (
              <p className={`${styles.note} ${local.status}`}>
                Falta para ativar: {p.missing.map((m) => MISSING_LABEL[m] ?? m).join(", ")}.
              </p>
            ) : null}

            {facts.length ? (
              <div className={local.status}>
                <KeyValues items={facts} />
              </div>
            ) : null}

            {canConfigure && p.flag_on ? (
              <>
                {canTest ? (
                  <form action={testPaymentProvider} className={local.testForm}>
                    {tenantField}
                    <input type="hidden" name="provider" value={p.provider} />
                    <button type="submit" className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
                      Testar credenciais
                    </button>
                  </form>
                ) : null}
                {p.enabled && !p.missing.length ? (
                  <details className={local.more}>
                    <summary>Alterar configuração</summary>
                    {configForm}
                  </details>
                ) : (
                  <div className={local.config}>{configForm}</div>
                )}
              </>
            ) : null}
          </Section>
        );
      })}
    </>
  );
}
