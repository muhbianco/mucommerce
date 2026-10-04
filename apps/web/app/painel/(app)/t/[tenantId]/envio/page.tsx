import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { isStoreMember, tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { KeyValues, PageHeader, Pill, Section } from "../ui";
import { connectAccount, saveOrigin, saveRules, saveServices, testAccount } from "./actions";
import local from "./envio.module.css";

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
  unmeasured: { id: string; name: string }[];
  oversized: { id: string; name: string; detail: string }[];
  last_test_ok?: boolean | null;
  last_test_detail?: string | null;
  /** As embalagens da loja (opcionais: sem elas, o pedido sai em caixa sob medida). */
  packing?: {
    packages_active: number;
    has_default: boolean;
    unfit: { id: string; name: string }[];
    orphans: { id: string; name: string }[];
  } | null;
}

interface ServiceOption {
  code: string;
  name: string;
  carrier: string;
  kind: string;
  available: boolean;
  grouped_volumes: boolean;
  requires_invoice: boolean;
  max_insurance_cents: number | null;
  max_weight_grams: number | null;
  offered: boolean;
  /** Por que a loja não pode oferecer (ex.: Jadlog saindo do Paraná sem nota fiscal). */
  blocked_reason?: string | null;
}

interface ServicesRead {
  services: ServiceOption[];
  all_offered: boolean;
  problem: string | null;
}

const SERVICE_KIND: Record<string, string> = {
  normal: "padrão",
  express: "expresso",
  economic: "econômico, para pacotes pequenos",
};

const SERVICES_PROBLEM: Record<string, string> = {
  not_connected: "Conecte a conta da transportadora acima para ver os serviços.",
  provider_unavailable: "A transportadora ainda não foi liberada para esta loja. Fale com a MuhBianco.",
  unavailable: "A transportadora não respondeu agora. Recarregue a página em instantes.",
};

/** "padrão · uma etiqueta por volume · seguro até R$ 3.000 · até 30 kg por volume". */
function serviceFacts(s: ServiceOption): string {
  const partes = [SERVICE_KIND[s.kind] ?? s.kind];
  partes.push(s.grouped_volumes ? "vários volumes numa etiqueta só" : "uma etiqueta por volume");
  if (s.max_insurance_cents) {
    partes.push(
      `seguro até ${new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL", maximumFractionDigits: 0 }).format(s.max_insurance_cents / 100)}`,
    );
  }
  if (s.max_weight_grams) {
    const kg = s.max_weight_grams / 1000;
    partes.push(`até ${kg.toLocaleString("pt-BR", { maximumFractionDigits: 1 })} kg por volume`);
  }
  return partes.filter(Boolean).join(" · ");
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

interface ShippingSettings {
  enabled?: boolean;
  origin?: Origin | null;
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
  "config:embalagem": "Cadastrar a embalagem padrão (em Embalagens)",
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
  // CPF tem 11 dígitos, CNPJ 14: é assim que a etiqueta sai (o mesmo teste do provedor).
  const documento = (origin.document ?? "").replace(/\D/g, "");
  const origemPR = (origin.state ?? "").trim().toUpperCase() === "PR";
  const pronto = status.connected && status.has_origin && status.flag_on;
  const embalagens = `/t/${context.tenant_id}/embalagens`;
  const carrier = PROVIDER_LABEL[status.provider] ?? status.provider;
  // A lista vem da conta na transportadora (cache de 10 min no servidor); sem conta, nem pergunta.
  const servicos =
    status.connected && status.flag_on
      ? await api<ServicesRead>(`/admin/tenants/${tenantId}/shipping/services`).catch(() => null)
      : null;

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

      {status.packing && status.packing.unfit.length > 0 ? (
        <Section
          title="Produtos que não cabem em nenhuma embalagem"
          description="Eles vão em caixa sob medida, do tamanho do pedido: você monta a caixa na hora de despachar."
        >
          <ul className={styles.steps}>
            {status.packing.unfit.map((produto) => (
              <li key={produto.id}>
                <Link href={`/t/${context.tenant_id}/produtos/${produto.id}#envio`}>{produto.name}</Link>
              </li>
            ))}
          </ul>
          <p className={styles.hint}>
            Se você tem uma caixa onde eles cabem, cadastre em <Link href={embalagens}>Embalagens</Link>; ou
            marque no produto que ele vai na embalagem dele.
          </p>
        </Section>
      ) : null}

      {status.packing && status.packing.orphans.length > 0 ? (
        <Section
          title="Produtos sem embalagem ativa"
          description="Eles só aceitavam embalagens que foram arquivadas; enquanto isso, o sistema escolhe sozinho."
        >
          <ul className={styles.steps}>
            {status.packing.orphans.map((produto) => (
              <li key={produto.id}>
                <Link href={`/t/${context.tenant_id}/produtos/${produto.id}#envio`}>{produto.name}</Link>
              </li>
            ))}
          </ul>
        </Section>
      ) : null}

      {status.oversized.length > 0 ? (
        <Section
          title="Produtos grandes demais para os Correios"
          description="PAC e SEDEX levam até 1 m por lado, 2 m somados e 30 kg. Acima disso eles somem da cotação, e sobra pouca ou nenhuma opção para o cliente."
        >
          <ul className={styles.steps}>
            {status.oversized.map((produto) => (
              <li key={produto.id}>
                <Link href={`/painel/t/${tenantId}/produtos/${produto.id}`}>{produto.name}</Link> —{" "}
                {produto.detail}
              </li>
            ))}
          </ul>
          <p className={styles.hint}>
            Confira a unidade: o campo é em milímetros, então uma caixa de 30 cm se escreve 300.
          </p>
        </Section>
      ) : null}

      {status.unmeasured.length > 0 ? (
        <Section
          title="Produtos sem peso ou medida"
          description="A transportadora cota pelo volume. Sem as quatro medidas, o carrinho não mostra frete nenhum para estes — e o cliente não descobre por quê."
        >
          <ul className={styles.steps}>
            {status.unmeasured.map((produto) => (
              <li key={produto.id}>
                <Link href={`/painel/t/${tenantId}/produtos/${produto.id}`}>{produto.name}</Link>
              </li>
            ))}
          </ul>
          <p className={styles.hint}>
            Peso com embalagem, e altura × largura × profundidade da caixa em que ele viaja.
          </p>
        </Section>
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
        title="Nota fiscal e declaração de conteúdo"
        description="Como as suas etiquetas saem, pelo documento de quem envia."
      >
        {documento.length === 11 ? (
          <p className={styles.hint}>
            Você envia como <strong>pessoa física (CPF)</strong>. Cada etiqueta sai com a declaração de
            conteúdo eletrônica (DC-e), que o Melhor Envio emite com os produtos do pedido — você não
            precisa de nota fiscal nem de certificado digital.
          </p>
        ) : documento.length === 14 ? (
          <p className={styles.hint}>
            Você envia como <strong>empresa (CNPJ), sem nota fiscal</strong>, com a declaração de conteúdo
            eletrônica (DC-e) que o Melhor Envio emite. Isso vale para MEI que vende para pessoa física e
            para empresa que não é contribuinte de ICMS. Envio com nota fiscal ainda não é suportado.
          </p>
        ) : (
          <p className={styles.note}>
            Informe o CPF ou o CNPJ de quem envia, no endereço de origem acima: sem ele a etiqueta não sai.
          </p>
        )}
        <p className={styles.hint}>
          A declaração (DACE) sai na mesma página da etiqueta: imprima as duas e mande a DACE junto com o
          pacote.
        </p>
        {origemPR ? (
          <p className={styles.note}>
            Saindo do Paraná, a Jadlog só aceita envio com nota fiscal: ela não aparece para os seus
            clientes.
          </p>
        ) : null}
        <p className={styles.hint}>
          A própria declaração traz impresso que é contribuinte de ICMS quem vende com frequência ou em
          volume de comércio. Se você ainda não tem MEI, confirme com um contador como enviar.
        </p>
      </Section>

      <Section
        title="Serviços oferecidos"
        description="Os marcados aparecem para o cliente no checkout, cada um com o preço e o prazo dele."
      >
        {!status.connected ? (
          <p className={styles.hint}>{SERVICES_PROBLEM.not_connected}</p>
        ) : servicos === null || servicos.problem ? (
          <>
            <p className={styles.note}>
              {SERVICES_PROBLEM[servicos?.problem ?? "unavailable"] ?? SERVICES_PROBLEM.unavailable}
            </p>
            {status.services.length ? (
              <p className={styles.hint}>
                Hoje a loja oferece: {status.services.filter((s) => s.active).map((s) => s.name).join(", ") || "nenhum"}.
              </p>
            ) : null}
          </>
        ) : (
          <form action={saveServices}>
            <input type="hidden" name="tenant_id" value={tenantId} />
            <fieldset className={local.services} disabled={readOnly}>
              <legend className={styles.hint}>
                {servicos.all_offered
                  ? "Hoje a loja oferece todos os serviços da sua conta. Desmarque os que não quer mostrar."
                  : "Desmarcado não aparece no checkout. Serviço novo da transportadora só entra quando você marcar."}
              </legend>
              {servicos.services.map((s) => (
                <label key={s.code} className={styles.check} data-service={s.code}>
                  <input
                    type="checkbox"
                    name="service"
                    value={s.code}
                    defaultChecked={s.offered && s.available}
                    disabled={!s.available}
                  />
                  <span>
                    {s.name} · {s.carrier}{" "}
                    {s.blocked_reason ? (
                      <Pill state="off">só com nota fiscal</Pill>
                    ) : !s.available ? (
                      <Pill state="off">indisponível na sua conta</Pill>
                    ) : null}
                    <span className={`${styles.fieldHint} ${local.facts}`}>
                      {s.blocked_reason ?? serviceFacts(s)}
                    </span>
                  </span>
                </label>
              ))}
            </fieldset>
            <div className={styles.formActions}>
              <button type="submit" className={styles.button} disabled={readOnly}>
                Salvar serviços
              </button>
            </div>
          </form>
        )}
      </Section>

      <Section
        title="Preço do frete"
        description="O acréscimo cobre embalagem e trabalho."
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

      <Section title="Embalagens" description="Opcional.">
        <p className={styles.hint}>
          Sem embalagem cadastrada, cada pedido sai numa <strong>caixa sob medida</strong>: o sistema
          calcula a menor caixa que leva o que foi vendido, e você monta a caixa nessas medidas. Se
          você usa caixas de tamanho fixo, cadastre em <Link href={embalagens}>Embalagens</Link> — lá
          também dá para testar como um pedido seria montado.
        </p>
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
