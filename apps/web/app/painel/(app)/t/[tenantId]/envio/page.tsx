import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { isStoreMember, tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { KeyValues, PageHeader, Pill, Section } from "../ui";
import { connectAccount, saveOrigin, saveRules, testAccount } from "./actions";

export const metadata: Metadata = { title: "Envio por transportadora" };

interface ShippingStatus {
  provider: string;
  flag_on: boolean;
  enabled: boolean;
  connected: boolean;
  has_origin: boolean;
  has_box: boolean;
  services: { code: string; name: string; carrier: string; active: boolean }[];
  missing: string[];
  last_test_ok?: boolean | null;
  last_test_detail?: string | null;
}

interface Origin {
  name?: string;
  postal_code?: string;
  address?: string;
  number?: string;
  complement?: string | null;
  district?: string;
  city?: string;
  state?: string;
  document?: string | null;
  phone?: string | null;
  email?: string | null;
}

interface Box {
  width_mm?: number;
  height_mm?: number;
  depth_mm?: number;
  max_weight_grams?: number;
  empty_weight_grams?: number;
}

interface ShippingSettings {
  enabled?: boolean;
  origin?: Origin | null;
  box?: Box | null;
  markup_percent?: number;
  markup_cents?: number;
  free_above_cents?: number | null;
  handling_days?: number;
}

const PROVIDER_LABEL: Record<string, string> = {
  melhorenvio: "Melhor Envio",
  fake: "Transportadora de teste",
};

/** O que ainda falta, em palavras de lojista (a API devolve códigos). */
const MISSING_LABEL: Record<string, string> = {
  "secret:access_token": "Conectar a conta da transportadora",
  "config:origem": "Informar o endereço de onde a mercadoria sai",
  "provider:indisponivel": "Transportadora indisponível nesta instalação",
};

function reais(cents?: number | null): string {
  if (cents === null || cents === undefined) return "";
  return (cents / 100).toFixed(2).replace(".", ",");
}

export default async function Shipping({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!tenantScopes(me, context.tenant_id).can("shipping:config")) notFound();
  // Etiqueta sai da carteira da loja, então conectar e gravar é só da equipe dela (o servidor
  // recusa o resto). O staff da MuhBianco abre para conferir por que o frete não cota.
  const readOnly = !isStoreMember(me, context.tenant_id);

  const status = await api<ShippingStatus>(`/admin/tenants/${tenantId}/shipping`);
  const cfg = ((context.settings.fulfillment ?? {}) as { shipping?: ShippingSettings }).shipping ?? {};
  const origin = cfg.origin ?? {};
  const box = cfg.box ?? {};
  const pronto = status.connected && status.has_origin && status.flag_on;
  const carrier = PROVIDER_LABEL[status.provider] ?? status.provider;

  return (
    <>
      <PageHeader
        eyebrow="Envio"
        title="Envio por transportadora"
        lead={
          <>
            Correios (PAC e SEDEX), Jadlog e Azul Cargo pelo <strong>{carrier}</strong>, com preço
            calculado pelo endereço de quem compra. Retirada e entrega por zona continuam na tela de{" "}
            <strong>Entrega e checkout</strong>.
          </>
        }
        actions={
          <Pill state={cfg.enabled && pronto ? "live" : status.connected ? "pending" : "off"}>
            {cfg.enabled && pronto ? "No ar" : status.connected ? "Falta configurar" : "Desligado"}
          </Pill>
        }
      />
      <Flash ok={ok} erro={erro} />

      {readOnly ? (
        <p className={styles.note}>
          Você está vendo esta loja como equipe MuhBianco. Conectar a conta e gravar a
          configuração é da equipe da loja — aqui dá para conferir o estado.
        </p>
      ) : null}

      {status.missing.length > 0 ? (
        <Section title="Para começar a cotar" description="Enquanto faltar isto, a loja não mostra frete no checkout.">
          <ul className={styles.steps}>
            {status.missing.map((item) => (
              <li key={item}>{MISSING_LABEL[item] ?? item}</li>
            ))}
          </ul>
          {!status.flag_on ? (
            <p className={styles.hint}>
              O módulo de envio ainda não foi liberado para esta loja. Fale com a MuhBianco.
            </p>
          ) : null}
        </Section>
      ) : null}

      <Section
        title="Conta da transportadora"
        description="A conta é sua: o frete sai do seu saldo e a etiqueta é emitida no seu nome."
      >
        <KeyValues
          items={[
            { label: "Transportadora", value: carrier },
            { label: "Conta", value: status.connected ? "Conectada" : "Não conectada" },
            ...(status.last_test_detail
              ? [
                  {
                    label: "Último teste",
                    value: `${status.last_test_ok ? "Funcionando" : "Não passou"} — ${status.last_test_detail}`,
                  },
                ]
              : []),
          ]}
        />
        <form action={connectAccount} className={styles.fields}>
          <input type="hidden" name="tenant_id" value={tenantId} />
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Token de acesso
            <input
              name="access_token"
              type="password"
              autoComplete="off"
              placeholder={status.connected ? "•••••••• (já conectado — cole outro para trocar)" : "Cole o token gerado no painel da transportadora"}
              required
            />
            <span className={styles.fieldHint}>
              Guardamos cifrado e nunca mostramos de volta. Ele compra etiqueta, então só o dono da
              loja pode mexer aqui.
            </span>
          </label>
          <div className={`${styles.formActions} ${styles.fieldWide}`}>
            <button type="submit" className={styles.button}>
              {status.connected ? "Trocar token" : "Conectar conta"}
            </button>
          </div>
        </form>
        {status.connected ? (
          <form action={testAccount}>
            <input type="hidden" name="tenant_id" value={tenantId} />
            <button type="submit" className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
              Testar agora
            </button>
          </form>
        ) : null}
      </Section>

      <Section
        title="De onde sai a mercadoria"
        description="Endereço de coleta/postagem. É daqui que a transportadora calcula o preço e o prazo."
      >
        <form action={saveOrigin}>
          <input type="hidden" name="tenant_id" value={tenantId} />
          <div className={styles.fields}>
            <label className={styles.field}>
              Nome de quem envia
              <input name="origin_name" defaultValue={origin.name ?? ""} maxLength={80} required />
            </label>
            <label className={styles.field}>
              CEP
              <input name="origin_postal_code" defaultValue={origin.postal_code ?? ""} placeholder="01001-000" required />
            </label>
            <label className={styles.field}>
              CPF ou CNPJ
              <input name="origin_document" defaultValue={origin.document ?? ""} placeholder="só números" />
              <span className={styles.fieldHint}>A etiqueta exige um dos dois.</span>
            </label>
            <label className={`${styles.field} ${styles.fieldWide}`}>
              Endereço
              <input name="origin_address" defaultValue={origin.address ?? ""} maxLength={120} required />
            </label>
            <label className={styles.field}>
              Número
              <input name="origin_number" defaultValue={origin.number ?? ""} maxLength={20} required />
            </label>
            <label className={styles.field}>
              Complemento
              <input name="origin_complement" defaultValue={origin.complement ?? ""} maxLength={80} />
            </label>
            <label className={styles.field}>
              Bairro
              <input name="origin_district" defaultValue={origin.district ?? ""} maxLength={80} required />
            </label>
            <label className={styles.field}>
              Cidade
              <input name="origin_city" defaultValue={origin.city ?? ""} maxLength={80} required />
            </label>
            <label className={styles.field}>
              UF
              <input name="origin_state" defaultValue={origin.state ?? ""} maxLength={2} placeholder="SP" required />
            </label>
            <label className={styles.field}>
              Telefone
              <input name="origin_phone" defaultValue={origin.phone ?? ""} maxLength={20} />
            </label>
            <label className={styles.field}>
              E-mail
              <input name="origin_email" type="email" defaultValue={origin.email ?? ""} maxLength={120} />
            </label>
          </div>
          <div className={styles.formActions}>
            <button type="submit" className={styles.button}>
              Salvar endereço
            </button>
          </div>
        </form>
      </Section>

      <Section
        title="Caixa e preço"
        description="A caixa padrão define o volume cotado; o acréscimo cobre embalagem e trabalho."
      >
        <form action={saveRules}>
          <input type="hidden" name="tenant_id" value={tenantId} />
          <label className={styles.check}>
            <input type="checkbox" name="enabled" defaultChecked={cfg.enabled ?? false} />
            <span>
              Oferecer envio por transportadora no checkout
              <span className={styles.fieldHint}>
                Desligar aqui não mexe em pedido nenhum: a loja volta a oferecer só retirada e
                entrega por zona.
              </span>
            </span>
          </label>
          <div className={styles.fields}>
            <label className={styles.field}>
              Largura da caixa (mm)
              <input name="box_width_mm" type="number" min={10} max={2000} defaultValue={box.width_mm ?? 200} />
            </label>
            <label className={styles.field}>
              Altura (mm)
              <input name="box_height_mm" type="number" min={10} max={2000} defaultValue={box.height_mm ?? 150} />
            </label>
            <label className={styles.field}>
              Profundidade (mm)
              <input name="box_depth_mm" type="number" min={10} max={2000} defaultValue={box.depth_mm ?? 100} />
            </label>
            <label className={styles.field}>
              Peso máximo da caixa (g)
              <input
                name="box_max_weight_grams"
                type="number"
                min={100}
                max={100000}
                defaultValue={box.max_weight_grams ?? 30000}
              />
            </label>
            <label className={styles.field}>
              Peso da caixa vazia (g)
              <input
                name="box_empty_weight_grams"
                type="number"
                min={0}
                max={10000}
                defaultValue={box.empty_weight_grams ?? 0}
              />
              <span className={styles.fieldHint}>A transportadora cobra o peso real, com embalagem.</span>
            </label>
            <label className={styles.field}>
              Acréscimo sobre o frete (%)
              <input name="markup_percent" type="number" min={0} max={100} defaultValue={cfg.markup_percent ?? 0} />
            </label>
            <label className={styles.field}>
              Acréscimo fixo (R$)
              <input name="markup" inputMode="decimal" defaultValue={reais(cfg.markup_cents)} placeholder="0,00" />
            </label>
            <label className={styles.field}>
              Frete grátis acima de (R$)
              <input name="free_above" inputMode="decimal" defaultValue={reais(cfg.free_above_cents)} placeholder="deixe vazio para não ter" />
              <span className={styles.fieldHint}>O cliente não paga; a etiqueta continua saindo do seu saldo.</span>
            </label>
            <label className={styles.field}>
              Dias de preparo
              <input name="handling_days" type="number" min={0} max={30} defaultValue={cfg.handling_days ?? 0} />
              <span className={styles.fieldHint}>Somados ao prazo que a transportadora informa.</span>
            </label>
          </div>
          <div className={styles.formActions}>
            <button type="submit" className={styles.button}>
              Salvar
            </button>
          </div>
        </form>
      </Section>

      <Section title="Como o pedido anda" description="O que acontece depois que alguém compra.">
        <ol className={styles.steps}>
          <li>
            <strong>O cliente escolhe o frete</strong> no checkout, pelo CEP dele. O preço que ele viu
            é o que entra no pedido.
          </li>
          <li>
            <strong>Você despacha</strong> na tela do pedido: a etiqueta é comprada e o código de
            rastreio aparece ali. Produto sem peso e medida não deixa cotar — a tela diz quais faltam.
          </li>
          <li>
            <strong>O pedido se fecha sozinho</strong> quando a transportadora confirma a entrega.
          </li>
        </ol>
      </Section>
    </>
  );
}
