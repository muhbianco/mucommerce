import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { parseInstant } from "@/lib/panel/format";
import { isStoreMember, tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { PageHeader, Pill, Section } from "../ui";
import {
  chooseProvider,
  connectAccount,
  saveOrigin,
  saveRules,
  saveServices,
  setShippingEnabled,
  testAccount,
} from "./actions";
import local from "./envio.module.css";

export const metadata: Metadata = { title: "Envio por transportadora" };

interface ProviderOption {
  name: string;
  label: string;
  token_help: string;
  flag_on: boolean;
  connected: boolean;
  active: boolean;
}

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
  last_test_at?: string | null;
  providers?: ProviderOption[];
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
  not_connected: "Conecte a conta da transportadora (passo 2) para ver os serviços.",
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

function reais(cents?: number | null): string {
  if (cents === null || cents === undefined) return "";
  return (cents / 100).toFixed(2).replace(".", ",");
}

function when(iso: string | null | undefined, zone: string): string {
  return (
    parseInstant(iso)?.toLocaleString("pt-BR", { timeZone: zone, dateStyle: "short", timeStyle: "short" }) ?? ""
  );
}

/** Um passo do caminho: feito, o próximo a fazer, ou esperando o anterior. */
function Step({ done, current, href, children }: { done: boolean; current: boolean; href: string; children: string }) {
  return (
    <li className={local.step} data-state={done ? "done" : current ? "current" : "todo"}>
      <span className={local.stepMark} aria-hidden="true">
        {done ? "✓" : "•"}
      </span>
      <a href={href}>{children}</a>
      <span className={local.stepState}>{done ? "feito" : current ? "a fazer agora" : "depois"}</span>
    </li>
  );
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
  const embalagens = `/t/${context.tenant_id}/embalagens`;
  const providers = status.providers ?? [];
  const active = providers.find((p) => p.active);
  const carrier = active?.label ?? status.provider;
  // A lista vem da conta na transportadora (cache de 10 min no servidor); sem conta, nem pergunta.
  const servicos =
    status.connected && status.flag_on
      ? await api<ServicesRead>(`/admin/tenants/${tenantId}/shipping/services`).catch(() => null)
      : null;
  const zone = context.timezone;

  // O caminho, em ordem. O primeiro que falta é o "a fazer agora".
  const passos = [
    { id: "transportadora", label: "Escolher a transportadora", done: Boolean(active) && status.flag_on },
    { id: "conta", label: "Conectar a conta dela", done: status.connected && status.last_test_ok !== false },
    { id: "origem", label: "Informar de onde a mercadoria sai", done: status.has_origin },
    {
      id: "servicos",
      label: "Escolher os serviços do checkout",
      done: status.connected && (servicos?.services.some((s) => s.offered) ?? status.services.length > 0),
    },
    { id: "preco", label: "Ligar o envio no checkout", done: Boolean(cfg.enabled) },
  ];
  const atual = passos.findIndex((p) => !p.done);
  const pronto = status.connected && status.has_origin && status.flag_on;
  const noAr = Boolean(cfg.enabled) && pronto;

  return (
    <>
      <PageHeader
        eyebrow="Envio"
        title="Envio por transportadora"
        lead={
          <>
            O cliente vê o preço e o prazo do frete pelo CEP dele, e você compra a etiqueta na tela do pedido.
            Retirada e entrega por zona continuam em <strong>Entrega e checkout</strong>.
          </>
        }
        actions={
          <Pill state={noAr ? "live" : status.connected ? "pending" : "off"}>
            {noAr ? "No ar" : status.connected ? "Falta configurar" : "Desligado"}
          </Pill>
        }
      />
      <Flash ok={ok} erro={erro} />

      {readOnly ? (
        <p className={styles.note}>
          Você está vendo esta loja como equipe MuhBianco. Conectar a conta e gravar a configuração é da equipe
          da loja — aqui dá para conferir o estado.
        </p>
      ) : null}

      <Section title="Passo a passo" description="Cinco passos. O que falta aparece marcado.">
        <ol className={local.progress}>
          {passos.map((p, i) => (
            <Step key={p.id} done={p.done} current={i === atual} href={`#${p.id}`}>
              {`${i + 1}. ${p.label}`}
            </Step>
          ))}
        </ol>
        {!status.flag_on ? (
          <p className={styles.note}>
            O envio por {carrier} está desligado em Módulos. Ligue lá para a transportadora poder cotar.
          </p>
        ) : null}
        <form action={setShippingEnabled} className={local.toggle}>
          <input type="hidden" name="tenant_id" value={tenantId} />
          <input type="hidden" name="enabled" value={cfg.enabled ? "0" : "1"} />
          <p className={styles.hint}>
            {cfg.enabled
              ? pronto
                ? "O checkout oferece envio por transportadora."
                : "O envio está ligado, mas o checkout só mostra frete quando os passos 1 a 3 estiverem feitos."
              : "O checkout ainda não oferece envio por transportadora."}
          </p>
          {readOnly ? null : (
            <button type="submit" className={cfg.enabled ? styles.buttonGhost : styles.button}>
              {cfg.enabled ? "Desligar envio no checkout" : "Ligar envio no checkout"}
            </button>
          )}
        </form>
      </Section>

      <Section
        id="transportadora"
        title="1. Transportadora"
        description="A conta é sua: o frete sai do seu saldo nela e a etiqueta é emitida no seu nome."
      >
        <div className={local.providers}>
          {providers.map((p) => (
            <div key={p.name} className={local.provider} data-active={p.active ? "1" : undefined}>
              <strong>{p.label}</strong>
              <span className={styles.fieldHint}>
                {!p.flag_on ? "desligada em Módulos" : p.connected ? "conta conectada" : "conta não conectada"}
              </span>
              {p.active ? (
                <Pill state="live">em uso</Pill>
              ) : readOnly || !p.flag_on ? null : (
                <form action={chooseProvider}>
                  <input type="hidden" name="tenant_id" value={tenantId} />
                  <input type="hidden" name="provider" value={p.name} />
                  <button type="submit" className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
                    Usar {p.label}
                  </button>
                </form>
              )}
            </div>
          ))}
        </div>
        <p className={styles.hint}>
          Trocar de transportadora não muda pedido já feito: ele é despachado e rastreado pela que cotou. Os
          serviços escolhidos (passo 4) começam do zero na nova.
        </p>
      </Section>

      <Section id="conta" title={`2. Conta ${carrier}`} description={active?.token_help}>
        {status.connected ? (
          <p className={styles.hint}>
            <strong>Conta conectada.</strong>{" "}
            {status.last_test_at
              ? `Último teste em ${when(status.last_test_at, zone)}: ${
                  status.last_test_ok ? "funcionando" : "não passou"
                }${status.last_test_detail ? ` (${status.last_test_detail})` : ""}.`
              : "Ainda não testada."}
          </p>
        ) : (
          <p className={styles.hint}>Cole o token da sua conta. Antes de guardar, a transportadora confere se ele vale.</p>
        )}
        <form action={connectAccount} className={styles.fields}>
          <input type="hidden" name="tenant_id" value={tenantId} />
          <input type="hidden" name="provider" value={status.provider} />
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Token de acesso
            <input
              name="access_token"
              type="password"
              autoComplete="off"
              placeholder={status.connected ? "•••••••• (cole outro para trocar)" : "Cole aqui o token da transportadora"}
              required
              disabled={readOnly}
            />
            <span className={styles.fieldHint}>
              Guardamos cifrado e nunca mostramos de volta. Ele compra etiqueta, então só o dono da loja mexe aqui.
            </span>
          </label>
          <div className={`${styles.formActions} ${styles.fieldWide}`}>
            <button type="submit" className={styles.button} disabled={readOnly}>
              {status.connected ? "Trocar token" : "Conectar conta"}
            </button>
          </div>
        </form>
        {status.connected && !readOnly ? (
          <form action={testAccount}>
            <input type="hidden" name="tenant_id" value={tenantId} />
            <button type="submit" className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
              Testar agora
            </button>
          </form>
        ) : null}
      </Section>

      <Section
        id="origem"
        title="3. De onde sai a mercadoria"
        description="Endereço de coleta ou postagem. É daqui que a transportadora calcula o preço e o prazo."
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
            <button type="submit" className={styles.button} disabled={readOnly}>
              Salvar endereço
            </button>
          </div>
        </form>
        <h3>Nota fiscal e declaração de conteúdo</h3>
        {documento.length === 11 ? (
          <p className={styles.hint}>
            Você envia como <strong>pessoa física (CPF)</strong>
            {status.provider === "melhorenvio"
              ? ". Cada etiqueta sai com a declaração de conteúdo eletrônica (DC-e), que o Melhor Envio emite com os produtos do pedido — você não precisa de nota fiscal nem de certificado digital."
              : ", com declaração de conteúdo. Confira na sua conta da transportadora como ela emite a declaração."}
          </p>
        ) : documento.length === 14 ? (
          <p className={styles.hint}>
            Você envia como <strong>empresa (CNPJ), sem nota fiscal</strong>, com declaração de conteúdo. Isso vale
            para MEI que vende para pessoa física e para empresa que não é contribuinte de ICMS. Envio com nota
            fiscal ainda não é suportado.
          </p>
        ) : (
          <p className={styles.note}>Informe o CPF ou o CNPJ de quem envia, acima: sem ele a etiqueta não sai.</p>
        )}
        {origemPR && status.provider === "melhorenvio" ? (
          <p className={styles.note}>
            Saindo do Paraná, a Jadlog só aceita envio com nota fiscal: ela não aparece para os seus clientes.
          </p>
        ) : null}
        <p className={styles.hint}>
          A declaração traz impresso que é contribuinte de ICMS quem vende com frequência ou em volume de comércio.
          Se você ainda não tem MEI, confirme com um contador como enviar.
        </p>
      </Section>

      <Section
        id="servicos"
        title="4. Serviços oferecidos"
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
                Hoje a loja oferece:{" "}
                {status.services
                  .filter((s) => s.active)
                  .map((s) => s.name)
                  .join(", ") || "nenhum"}
                .
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
                    <span className={`${styles.fieldHint} ${local.facts}`}>{s.blocked_reason ?? serviceFacts(s)}</span>
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

      <Section id="preco" title="5. Preço do frete" description="O acréscimo cobre embalagem e trabalho.">
        <form action={saveRules}>
          <input type="hidden" name="tenant_id" value={tenantId} />
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
              <input
                name="free_above"
                inputMode="decimal"
                defaultValue={reais(cfg.free_above_cents)}
                placeholder="deixe vazio para não ter"
              />
              <span className={styles.fieldHint}>O cliente não paga; a etiqueta continua saindo do seu saldo.</span>
            </label>
            <label className={styles.field}>
              Dias de preparo
              <input name="handling_days" type="number" min={0} max={30} defaultValue={cfg.handling_days ?? 0} />
              <span className={styles.fieldHint}>Somados ao prazo que a transportadora informa.</span>
            </label>
          </div>
          <div className={styles.formActions}>
            <button type="submit" className={styles.button} disabled={readOnly}>
              Salvar preço
            </button>
          </div>
        </form>
        <p className={styles.hint}>Ligar ou desligar o envio no checkout fica no topo, no passo a passo.</p>
      </Section>

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
            Se você tem uma caixa onde eles cabem, cadastre em <Link href={embalagens}>Embalagens</Link>; ou marque no
            produto que ele vai na embalagem dele.
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
                <Link href={`/painel/t/${tenantId}/produtos/${produto.id}`}>{produto.name}</Link> — {produto.detail}
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
          <p className={styles.hint}>Peso com embalagem, e altura × largura × profundidade da caixa em que ele viaja.</p>
        </Section>
      ) : null}

      <Section title="Embalagens" description="Opcional.">
        <p className={styles.hint}>
          Sem embalagem cadastrada, cada pedido sai numa <strong>caixa sob medida</strong>: o sistema calcula a menor
          caixa que leva o que foi vendido, e você monta a caixa nessas medidas. Se você usa caixas de tamanho fixo,
          cadastre em <Link href={embalagens}>Embalagens</Link> — lá também dá para testar como um pedido seria montado.
        </p>
      </Section>

      <Section title="Como o pedido anda" description="O que acontece depois que alguém compra.">
        <ol className={styles.steps}>
          <li>
            <strong>O cliente escolhe o frete</strong> no checkout, pelo CEP dele. O preço que ele viu é o que entra
            no pedido.
          </li>
          <li>
            <strong>Você despacha</strong> na tela do pedido: a etiqueta é comprada e o código de rastreio aparece
            ali. Produto sem peso e medida não deixa cotar — a tela diz quais faltam.
          </li>
          <li>
            <strong>O pedido se fecha sozinho</strong> quando a transportadora confirma a entrega.
          </li>
        </ol>
      </Section>
    </>
  );
}
