import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import { PAYMENT_METHOD_LABEL, PAYMENT_PROVIDERS, type PaymentProviderRead } from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { savePaymentProvider, testPaymentProvider } from "./actions";

export const metadata: Metadata = { title: "Pagamentos" };

const MISSING_LABEL: Record<string, string> = {
  "secret:access_token": "access token",
  "secret:webhook_secret": "assinatura secreta dos webhooks",
  "public:public_key": "public key",
  "public:handle": "InfiniteTag",
};

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
  const providers = await api<PaymentProviderRead[]>(`/admin/tenants/${context.tenant_id}/payments/providers`);
  const dateTime = (iso: string) =>
    new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short", timeZone: context.timezone }).format(
      new Date(iso),
    );
  const tenantField = <input type="hidden" name="tenant_id" value={context.tenant_id} />;

  return (
    <>
      <h2>Pagamentos</h2>
      <Flash ok={ok} erro={erro} />
      <p className="muted">
        O dinheiro das vendas cai direto na conta da loja no meio de pagamento escolhido. As chaves ficam guardadas
        cifradas e nunca aparecem de novo nesta tela.
      </p>
      {providers.map((p) => {
        const spec = PAYMENT_PROVIDERS[p.provider];
        if (!spec) return null;
        const methods = p.methods ?? spec.methods;
        return (
          <section key={p.provider} className={styles.card}>
            <h2>
              {spec.label} {p.enabled ? "— ativo" : "— desligado"}
              {p.is_default ? " (padrão)" : ""}
            </h2>
            {spec.hint ? <p className="muted">{spec.hint}</p> : null}
            {!p.flag_on ? (
              <p className="muted">Este meio não está liberado para a loja. Fale com o suporte para liberar.</p>
            ) : null}
            {p.missing.length ? (
              <p className="muted">Falta para ativar: {p.missing.map((m) => MISSING_LABEL[m] ?? m).join(", ")}.</p>
            ) : null}
            {spec.secrets.length ? (
              <p>
                Endereço de notificações (cadastre no painel do {spec.label}): <code>{p.webhook_url}</code>
                {p.last_webhook_at
                  ? ` — último aviso recebido em ${dateTime(p.last_webhook_at)}`
                  : " — nenhum aviso recebido ainda"}
              </p>
            ) : null}
            {p.last_test_at ? (
              <p className={p.last_test_ok ? styles.ok : styles.error}>
                Teste de {dateTime(p.last_test_at)}:{" "}
                {p.last_test_ok ? "credenciais funcionando." : `falhou (${p.last_test_error ?? "erro"}).`}
              </p>
            ) : null}

            {canConfigure && p.flag_on ? (
              <>
                <form action={savePaymentProvider} className={styles.form}>
                  {tenantField}
                  <input type="hidden" name="provider" value={p.provider} />
                  {spec.public.map(({ key, label }) => (
                    <label key={key}>
                      {label}
                      <input name={`public_${key}`} maxLength={200} defaultValue={p.public_config[key] ?? ""} autoComplete="off" />
                    </label>
                  ))}
                  {spec.secrets.map(({ key, label }) => (
                    <label key={key}>
                      {label}
                      <input
                        type="password"
                        name={`secret_${key}`}
                        maxLength={500}
                        autoComplete="off"
                        placeholder={p.secrets[key] ? `guardado (${p.secrets[key].masked}) — em branco mantém` : ""}
                      />
                    </label>
                  ))}
                  <fieldset>
                    <legend>Meios oferecidos</legend>
                    {spec.methods.map((m) => (
                      <label key={m}>
                        <input type="checkbox" name={`method_${m}`} defaultChecked={methods.includes(m)} />{" "}
                        {PAYMENT_METHOD_LABEL[m] ?? m}
                      </label>
                    ))}
                  </fieldset>
                  {spec.methods.includes("card") ? (
                    <label>
                      Parcelas no cartão (até)
                      <input type="number" name="installments_max" min={1} max={12} defaultValue={p.installments_max} />
                    </label>
                  ) : null}
                  {spec.secrets.length ? (
                    <label>
                      <input type="checkbox" name="sandbox" defaultChecked={p.sandbox} /> Credenciais de teste (sandbox)
                    </label>
                  ) : null}
                  <label>
                    <input type="checkbox" name="is_default" defaultChecked={p.is_default} /> Meio padrão
                  </label>
                  <label>
                    <input type="checkbox" name="enabled" defaultChecked={p.enabled} /> Ativo na loja
                  </label>
                  <button type="submit">Salvar</button>
                </form>
                {Object.keys(p.secrets).length || !spec.secrets.length ? (
                  <form action={testPaymentProvider}>
                    {tenantField}
                    <input type="hidden" name="provider" value={p.provider} />
                    <button type="submit" className="muted">
                      Testar credenciais
                    </button>
                  </form>
                ) : null}
              </>
            ) : !canConfigure ? (
              <p className="muted">Só o dono da loja altera os meios de pagamento.</p>
            ) : null}
          </section>
        );
      })}
    </>
  );
}
