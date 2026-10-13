import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { formatMoney, moneyInput, parseInstant } from "@/lib/panel/format";
import { isStoreMember, tenantScopes } from "@/lib/panel/scopes";
import { loadTenantContext } from "@/lib/panel/tenant-context";
import type {
  CheckoutSettings,
  DeliveryOrigin,
  DeliveryWindow,
  DeliveryZone,
  FulfillmentSettings,
  PickupLocation,
  UberStatus,
} from "@/lib/panel/types";

import styles from "../../../../panel.module.css";
import { Flash } from "../flash";
import { PageHeader, Pill, Section, TableWrap } from "../ui";
import { connectUber, disconnectUber, saveCheckout, saveFulfillment, saveUberSettings, testUber } from "./actions";
import local from "./entrega.module.css";

export const metadata: Metadata = { title: "Entrega e checkout" };

const WEEKDAYS = ["Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo"];

/** Campos de um local de retirada (linha `i` do formulário; `loc` vazio = local novo). */
function LocationFields({ loc, i }: { loc: PickupLocation | null; i: number }) {
  return (
    <>
      <input type="hidden" name={`loc_id_${i}`} value={loc?.id ?? ""} />
      <div className={styles.fields}>
        <label className={styles.field}>
          Nome do local
          <input name={`loc_name_${i}`} maxLength={80} defaultValue={loc?.name ?? ""} placeholder="Ex.: Loja do centro" />
          <span className={styles.fieldHint}>
            {loc ? "Apague o nome e salve para remover este local." : "Deixe vazio se não for usar."}
          </span>
        </label>
        <div className={local.checkCell}>
          <label className={styles.check}>
            <input type="checkbox" name={`loc_active_${i}`} defaultChecked={loc?.active ?? true} /> Ativo
          </label>
        </div>
        <label className={`${styles.field} ${styles.fieldWide}`}>
          Endereço
          <input name={`loc_address_${i}`} maxLength={300} defaultValue={loc?.address ?? ""} />
        </label>
        <label className={`${styles.field} ${styles.fieldWide}`}>
          Instruções para retirar
          <input
            name={`loc_instructions_${i}`}
            maxLength={300}
            defaultValue={loc?.instructions ?? ""}
            placeholder="Ex.: toque o interfone 12"
          />
        </label>
      </div>
    </>
  );
}

/** Campos de uma zona de entrega (linha `i`; `zone` vazio = zona nova). */
function ZoneFields({ zone, i }: { zone: DeliveryZone | null; i: number }) {
  return (
    <>
      <input type="hidden" name={`zone_id_${i}`} value={zone?.id ?? ""} />
      <div className={styles.fields}>
        <label className={styles.field}>
          Nome da zona
          <input name={`zone_name_${i}`} maxLength={80} defaultValue={zone?.name ?? ""} placeholder="Ex.: Centro" />
          <span className={styles.fieldHint}>
            {zone ? "Apague o nome e salve para remover esta zona." : "Deixe vazio se não for usar."}
          </span>
        </label>
        <label className={styles.field}>
          Tipo
          <select name={`zone_kind_${i}`} defaultValue={zone?.kind ?? "cep_ranges"}>
            <option value="cep_ranges">Faixas de CEP</option>
            <option value="districts">Bairros</option>
          </select>
        </label>
        <label className={styles.field}>
          Taxa de entrega (R$)
          <input name={`zone_fee_${i}`} inputMode="decimal" defaultValue={moneyInput(zone?.fee_cents ?? null)} placeholder="0,00" />
          <span className={styles.fieldHint}>Vazio = entrega grátis.</span>
        </label>
        <label className={styles.field}>
          Pedido mínimo (R$)
          <input
            name={`zone_min_${i}`}
            inputMode="decimal"
            defaultValue={moneyInput(zone?.min_order_cents ?? null)}
            placeholder="Opcional"
          />
        </label>
        <label className={styles.field}>
          Prazo (minutos)
          <input name={`zone_eta_${i}`} type="number" min={0} defaultValue={zone?.eta_minutes ?? ""} placeholder="Opcional" />
        </label>
        <label className={styles.field}>
          Quem entrega
          <select name={`zone_courier_${i}`} defaultValue={zone?.courier ?? "own"}>
            <option value="own">Minha equipe</option>
            <option value="uber">Uber (chamada no &quot;Pronto&quot;)</option>
          </select>
          <span className={styles.fieldHint}>Uber: configure a conta e a coleta em Entrega pela Uber, abaixo.</span>
        </label>
        <div className={local.checkCell}>
          <label className={styles.check}>
            <input type="checkbox" name={`zone_active_${i}`} defaultChecked={zone?.active ?? true} /> Ativa
          </label>
        </div>
      </div>

      <div className={`${local.group} ${local.onlyCep}`}>
        <p className={local.groupLabel}>Por faixa de CEP</p>
        <div className={styles.fields}>
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Faixas de CEP
            <textarea
              name={`zone_ceps_${i}`}
              rows={2}
              defaultValue={(zone?.cep_ranges ?? []).map((r) => `${r.start} a ${r.end}`).join("\n")}
            />
            <span className={styles.fieldHint}>
              Uma por linha, como <code>01000-000 a 01099-999</code>.
            </span>
          </label>
        </div>
      </div>

      <div className={`${local.group} ${local.onlyDistricts}`}>
        <p className={local.groupLabel}>Por bairro</p>
        <div className={styles.fields}>
          <label className={styles.field}>
            Cidade
            <input name={`zone_city_${i}`} maxLength={80} defaultValue={zone?.city ?? ""} />
          </label>
          <label className={styles.field}>
            UF
            <input name={`zone_state_${i}`} maxLength={2} defaultValue={zone?.state ?? ""} placeholder="SP" />
          </label>
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Bairros
            <textarea name={`zone_districts_${i}`} rows={2} defaultValue={(zone?.districts ?? []).join("\n")} />
            <span className={styles.fieldHint}>Um por linha.</span>
          </label>
        </div>
      </div>
    </>
  );
}

function zoneSummary(zone: DeliveryZone): string {
  const parts = [
    zone.kind === "districts" ? "Bairros" : "Faixas de CEP",
    zone.courier === "uber" ? "entrega pela Uber" : "equipe da loja",
    zone.fee_cents ? `taxa ${formatMoney(zone.fee_cents)}` : "entrega grátis",
  ];
  if (zone.min_order_cents) parts.push(`mínimo ${formatMoney(zone.min_order_cents)}`);
  if (zone.eta_minutes) parts.push(`${zone.eta_minutes} min`);
  return parts.join(" · ");
}

const FALTA: Record<UberStatus["missing"][number], string> = {
  chaves: "conectar a conta Uber Direct da loja",
  coleta: "informar o endereço de coleta, com telefone",
  zona: 'marcar ao menos uma zona ativa com "Quem entrega: Uber"',
};

function when(iso: string | null | undefined, zone: string): string {
  return (
    parseInstant(iso)?.toLocaleString("pt-BR", { timeZone: zone, dateStyle: "short", timeStyle: "short" }) ?? ""
  );
}

/**
 * Entrega pela Uber (docs/15): a conta é da loja, o cliente paga a taxa da zona e a corrida sai
 * da conta Uber dela. Formulários separados porque têm donos diferentes: as chaves (só o dono,
 * conferidas na Uber) e a coleta com a política de chamada (configuração da loja).
 */
function UberSection({
  tenantId,
  status,
  owner,
  reader,
  origin,
  autoDispatch,
  maxFee,
  zone,
}: {
  tenantId: string;
  status: UberStatus | null;
  owner: boolean;
  reader: boolean;
  origin: Partial<DeliveryOrigin>;
  autoDispatch: boolean;
  maxFee: number | null;
  zone: string;
}) {
  const tenantField = <input type="hidden" name="tenant_id" value={tenantId} />;
  const pronta = status !== null && status.missing.length === 0 && status.last_test_ok !== false;
  return (
    <Section
      id="uber"
      title="Entrega pela Uber"
      description="Um entregador da Uber leva os pedidos das zonas marcadas como Uber"
      actions={
        status ? (
          <Pill state={pronta ? "live" : status.connected ? "warn" : "off"}>
            {pronta ? "Pronta" : status.connected ? "Falta configurar" : "Desconectada"}
          </Pill>
        ) : undefined
      }
    >
      <p className={local.intro}>
        Quando um pedido de zona da Uber fica <strong>Pronto</strong>, chamamos um entregador da{" "}
        <strong>sua conta Uber Direct</strong> para o endereço do cliente. O cliente paga a taxa da zona; a corrida é
        cobrada pela Uber na sua conta. A Uber só atende até cerca de 5 km da coleta: monte as zonas da Uber perto da
        loja.
      </p>

      {!reader ? (
        <p className={styles.note}>Só o dono da loja conecta a conta da Uber.</p>
      ) : status === null ? (
        <p className={styles.note}>Não foi possível ler a situação da Uber agora. Recarregue a página.</p>
      ) : (
        <>
          {status.missing.length ? (
            <div className={styles.note}>
              Para o &quot;Pronto&quot; chamar a Uber, falta:
              <ul>
                {status.missing.map((item) => (
                  <li key={item}>{FALTA[item]}</li>
                ))}
              </ul>
            </div>
          ) : null}

          <h4 className={local.subhead}>1. Conta Uber Direct</h4>
          {status.connected ? (
            <p className={styles.hint}>
              <strong>Conta conectada</strong> (Customer ID {status.masked.customer_id ?? "••••"},{" "}
              {status.environment === "test" ? "chaves de teste" : "chaves de produção"}).{" "}
              {status.last_test_at
                ? `Último teste em ${when(status.last_test_at, zone)}: ${
                    status.last_test_ok ? "funcionando" : "não passou"
                  }${status.last_test_detail ? ` (${status.last_test_detail})` : ""}.`
                : "Ainda não testada."}
            </p>
          ) : (
            <p className={styles.hint}>
              No painel da Uber Direct, aba <strong>Developer</strong>, copie o Customer ID e crie o Client ID e o
              Client Secret. Antes de guardar, conferimos as três na Uber.
            </p>
          )}
          <form action={connectUber} className={styles.fields}>
            {tenantField}
            <label className={styles.field}>
              Customer ID
              <input
                name="uber_customer_id"
                autoComplete="off"
                required
                maxLength={200}
                disabled={!owner}
                placeholder={status.connected ? "cole de novo para trocar" : ""}
              />
            </label>
            <label className={styles.field}>
              Client ID
              <input name="uber_client_id" autoComplete="off" required maxLength={200} disabled={!owner} />
            </label>
            <label className={styles.field}>
              Client Secret
              <input
                name="uber_client_secret"
                type="password"
                autoComplete="off"
                required
                maxLength={200}
                disabled={!owner}
              />
            </label>
            <label className={styles.field}>
              Ambiente das chaves
              <select name="uber_environment" defaultValue={status.environment} disabled={!owner}>
                <option value="production">Produção (entregador de verdade)</option>
                <option value="test">Teste (entregador simulado)</option>
              </select>
              <span className={styles.fieldHint}>
                A Uber não diz de qual ambiente são as chaves: escolha o mesmo de onde você copiou.
              </span>
            </label>
            <p className={`${styles.fieldHint} ${styles.fieldWide}`}>
              Guardamos cifrado e nunca mostramos de volta. As chaves chamam corrida na sua conta, então só o dono da loja
              mexe aqui.
            </p>
            <div className={`${styles.formActions} ${styles.fieldWide}`}>
              <button type="submit" className={styles.button} disabled={!owner}>
                {status.connected ? "Trocar chaves" : "Conectar conta"}
              </button>
            </div>
          </form>
          {status.connected && owner ? (
            <div className={styles.formActions}>
              <form action={testUber}>
                {tenantField}
                <button type="submit" className={`${styles.buttonGhost} ${styles.buttonSmall}`}>
                  Testar agora
                </button>
              </form>
              <form action={disconnectUber}>
                {tenantField}
                <button type="submit" className={`${styles.buttonDanger} ${styles.buttonSmall}`}>
                  Desconectar
                </button>
              </form>
            </div>
          ) : null}
        </>
      )}

      <h4 className={local.subhead}>2. Coleta e chamada</h4>
      <form action={saveUberSettings}>
        {tenantField}
        <div className={styles.fields}>
          <label className={styles.field}>
            Nome na coleta
            <input name="origin_name" maxLength={80} defaultValue={origin.name ?? ""} placeholder="Ex.: Doceria da Ana" />
          </label>
          <label className={styles.field}>
            Telefone da coleta
            <input
              name="origin_phone"
              inputMode="tel"
              maxLength={20}
              defaultValue={origin.phone ?? ""}
              placeholder="(11) 99999-9999"
            />
            <span className={styles.fieldHint}>A Uber exige: é para onde o entregador liga.</span>
          </label>
          <label className={styles.field}>
            CEP
            <input name="origin_cep" inputMode="numeric" maxLength={9} defaultValue={origin.postal_code ?? ""} />
          </label>
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Rua
            <input name="origin_address" maxLength={120} defaultValue={origin.address ?? ""} />
          </label>
          <label className={styles.field}>
            Número
            <input name="origin_number" maxLength={20} defaultValue={origin.number ?? ""} />
          </label>
          <label className={styles.field}>
            Complemento
            <input name="origin_complement" maxLength={80} defaultValue={origin.complement ?? ""} />
          </label>
          <label className={styles.field}>
            Bairro
            <input name="origin_district" maxLength={80} defaultValue={origin.district ?? ""} />
          </label>
          <label className={styles.field}>
            Cidade
            <input name="origin_city" maxLength={80} defaultValue={origin.city ?? ""} />
          </label>
          <label className={styles.field}>
            UF
            <input name="origin_state" maxLength={2} defaultValue={origin.state ?? ""} placeholder="SP" />
          </label>
          <label className={`${styles.field} ${styles.fieldWide}`}>
            Instrução para o entregador
            <input
              name="origin_notes"
              maxLength={280}
              defaultValue={origin.notes ?? ""}
              placeholder="Ex.: retirar no balcão dos fundos"
            />
          </label>
          <label className={`${styles.check} ${styles.fieldWide}`}>
            <input type="checkbox" name="uber_auto_dispatch" defaultChecked={autoDispatch} />
            <span className={local.checkText}>
              Chamar o entregador sozinho quando o pedido ficar Pronto
              <span className={styles.fieldHint}>Desligado, você chama a Uber no pedido, quando quiser.</span>
            </span>
          </label>
          <label className={styles.field}>
            Teto por corrida (R$)
            <input name="uber_max_fee" inputMode="decimal" defaultValue={moneyInput(maxFee)} placeholder="Sem teto" />
            <span className={styles.fieldHint}>Se a Uber cotar acima disso, não chamamos sozinhos: você confirma no pedido.</span>
          </label>
        </div>
        <p className={styles.hint}>Para tirar a coleta, apague o nome e a rua e salve.</p>
        <div className={styles.formActions}>
          <button type="submit" className={styles.button}>
            Salvar coleta e chamada
          </button>
        </div>
      </form>
    </Section>
  );
}

export default async function Fulfillment({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId } = await params;
  const { ok, erro } = await searchParams;
  const [context, me] = await Promise.all([loadTenantContext(tenantId), requireMe()]);
  if (!tenantScopes(me, context.tenant_id).can("settings:write")) notFound();
  const { features } = context;
  if (!(features.checkout || features.pickup || features.delivery)) notFound();

  const cfg = (context.settings.fulfillment ?? {}) as Partial<FulfillmentSettings>;
  const checkout = (context.settings.checkout ?? {}) as Partial<CheckoutSettings>;
  const locations: (PickupLocation | null)[] = [...(cfg.pickup?.locations ?? []), ...Array(2).fill(null)].slice(0, 10);
  const zones: (DeliveryZone | null)[] = [...(cfg.delivery?.zones ?? []), null].slice(0, 50);
  const windows: (DeliveryWindow | null)[] = [...(cfg.scheduling?.windows ?? []), ...Array(2).fill(null)].slice(0, 28);
  const tenantField = <input type="hidden" name="tenant_id" value={context.tenant_id} />;

  // As chaves da Uber chamam corrida na conta da loja: só o dono conecta (o servidor recusa o
  // resto). O staff da MuhBianco lê para conferir por que a Uber não foi chamada.
  const scopes = tenantScopes(me, context.tenant_id);
  const uberReader = Boolean(features.delivery) && scopes.can("shipping:config");
  const uberOwner = uberReader && isStoreMember(me, context.tenant_id);
  const uber = uberReader
    ? await api<UberStatus>(`/admin/tenants/${tenantId}/delivery/uber`).catch(() => null)
    : null;
  const origin: Partial<DeliveryOrigin> = cfg.delivery?.origin ?? {};

  // Linhas já salvas abrem sob demanda; as vazias (para criar) ficam num cartão "Adicionar".
  // O índice `i` é o da lista inteira: é ele que vai no nome dos campos.
  const locationRows = locations.map((loc, i) => ({ loc, i }));
  const savedLocations = locationRows.filter((row) => row.loc !== null);
  const newLocations = locationRows.filter((row) => row.loc === null);
  const zoneRows = zones.map((zone, i) => ({ zone, i }));
  const savedZones = zoneRows.filter((row) => row.zone !== null);
  const newZones = zoneRows.filter((row) => row.zone === null);

  return (
    <>
      <PageHeader
        eyebrow="Entrega e checkout"
        title="Como o pedido chega ao cliente"
        lead="Defina onde o cliente retira, até onde você entrega, em que horários e as regras para pagar e cancelar."
      />
      <Flash ok={ok} erro={erro} />
      {!features.pickup && !features.delivery ? (
        <p className={styles.note}>Retirada e entrega estão desligadas para a loja. Fale com o suporte para ligar.</p>
      ) : null}

      <form action={saveFulfillment}>
        {tenantField}
        <input type="hidden" name="loc_rows" value={locations.length} />
        <input type="hidden" name="zone_rows" value={zones.length} />
        <input type="hidden" name="win_rows" value={windows.length} />

        <Section
          title="Retirada na loja"
          description="O cliente busca o pedido num endereço seu"
          actions={features.pickup ? undefined : <Pill state="off">Módulo desligado</Pill>}
        >
          <div className={local.toggle}>
            <label className={styles.check}>
              <input type="checkbox" name="pickup_enabled" defaultChecked={cfg.pickup?.enabled ?? true} />
              <span className={local.checkText}>
                Oferecer retirada
                <span className={styles.fieldHint}>
                  {features.pickup
                    ? "Aparece como opção no checkout, com os locais ativos abaixo."
                    : "O módulo de retirada está desligado para a loja: fale com o suporte para ligar."}
                </span>
              </span>
            </label>
          </div>
          <div className={local.list}>
            {savedLocations.map(({ loc, i }) =>
              loc ? (
                <details key={loc.id} className={local.item}>
                  <summary>
                    <span className={local.itemTitle}>{loc.name}</span>
                    <span className={local.itemSub}>{loc.address}</span>
                    <Pill state={loc.active ? "live" : "off"}>{loc.active ? "Ativo" : "Desligado"}</Pill>
                  </summary>
                  <div className={local.itemBody}>
                    <LocationFields loc={loc} i={i} />
                  </div>
                </details>
              ) : null,
            )}
            {newLocations.length ? (
              <details className={`${local.item} ${local.add}`} open={features.pickup && savedLocations.length === 0}>
                <summary>{savedLocations.length ? "Adicionar outro local" : "Cadastrar um local de retirada"}</summary>
                <div className={local.itemBody}>
                  {newLocations.map(({ i }) => (
                    <div key={`novo-${i}`} className={local.newRow}>
                      <p className={local.groupLabel}>Novo local</p>
                      <LocationFields loc={null} i={i} />
                    </div>
                  ))}
                </div>
              </details>
            ) : null}
          </div>
        </Section>

        <Section
          title="Entrega própria"
          description="Até onde você leva e quanto cobra"
          actions={features.delivery ? undefined : <Pill state="off">Módulo desligado</Pill>}
        >
          <div className={local.toggle}>
            <label className={styles.check}>
              <input type="checkbox" name="delivery_enabled" defaultChecked={cfg.delivery?.enabled ?? false} />
              <span className={local.checkText}>
                Oferecer entrega
                <span className={styles.fieldHint}>
                  {features.delivery
                    ? "O cliente informa o endereço e vê a taxa da zona que atende."
                    : "O módulo de entrega está desligado para a loja: fale com o suporte para ligar."}
                </span>
              </span>
            </label>
          </div>
          <p className={local.intro}>
            Monte zonas por faixa de CEP (uma por linha, <code>01000-000 a 01099-999</code>) ou por bairro (um por
            linha, com cidade e UF). Vale a primeira zona ativa que atende o endereço; faixas de CEP são conferidas
            antes dos bairros.
          </p>
          <div className={local.list}>
            {savedZones.map(({ zone, i }) =>
              zone ? (
                <details key={zone.id} className={`${local.item} ${local.zone}`}>
                  <summary>
                    <span className={local.itemTitle}>{zone.name}</span>
                    <span className={local.itemSub}>{zoneSummary(zone)}</span>
                    <Pill state={zone.active ? "live" : "off"}>{zone.active ? "Ativa" : "Desligada"}</Pill>
                  </summary>
                  <div className={local.itemBody}>
                    <ZoneFields zone={zone} i={i} />
                  </div>
                </details>
              ) : null,
            )}
            {newZones.map(({ i }) => (
              <details
                key={`nova-${i}`}
                className={`${local.item} ${local.add} ${local.zone}`}
                open={features.delivery && savedZones.length === 0}
              >
                <summary>{savedZones.length ? "Adicionar outra zona" : "Criar a primeira zona de entrega"}</summary>
                <div className={local.itemBody}>
                  <ZoneFields zone={null} i={i} />
                </div>
              </details>
            ))}
          </div>
          {savedZones.length > 0 && newZones.length > 0 ? (
            <p className={styles.hint}>Para criar mais de uma zona, salve e adicione a próxima.</p>
          ) : null}
        </Section>

        <Section title="Pedido mínimo e horários" description="Vale para retirada e entrega">
          <div className={styles.fields}>
            <label className={styles.field}>
              Pedido mínimo da loja (R$)
              <input
                name="min_order"
                inputMode="decimal"
                defaultValue={moneyInput(cfg.min_order_cents ?? null)}
                placeholder="Sem mínimo"
              />
              <span className={styles.fieldHint}>Vazio = qualquer valor. Cada zona pode pedir um mínimo próprio.</span>
            </label>
          </div>

          <h4 className={local.subhead}>Agendamento</h4>
          <div className={styles.fields}>
            <label className={`${styles.check} ${styles.fieldWide}`}>
              <input type="checkbox" name="scheduling_enabled" defaultChecked={cfg.scheduling?.enabled ?? false} />
              <span className={local.checkText}>
                Cliente escolhe o horário
                <span className={styles.fieldHint}>Ele escolhe um dos horários da tabela abaixo ao fechar o pedido.</span>
              </span>
            </label>
            <label className={styles.field}>
              Antecedência mínima (minutos)
              <input
                name="min_lead_minutes"
                type="number"
                min={0}
                defaultValue={cfg.scheduling?.min_lead_minutes ?? 60}
              />
              <span className={styles.fieldHint}>Tempo mínimo entre o pedido e o horário escolhido.</span>
            </label>
            <label className={styles.field}>
              Dias à frente
              <input name="days_ahead" type="number" min={1} max={30} defaultValue={cfg.scheduling?.days_ahead ?? 7} />
              <span className={styles.fieldHint}>Até quantos dias o cliente pode agendar (1 a 30).</span>
            </label>
          </div>

          <h4 className={local.subhead}>Horários de atendimento</h4>
          <TableWrap>
            <table className={`${styles.table} ${local.windows}`}>
              <thead>
                <tr>
                  <th>Dia</th>
                  <th>Das</th>
                  <th>Às</th>
                  <th className={local.center}>Retirada</th>
                  <th className={local.center}>Entrega</th>
                </tr>
              </thead>
              <tbody>
                {windows.map((window, i) => (
                  <tr key={i}>
                    <td>
                      <select name={`win_weekday_${i}`} defaultValue={window?.weekday ?? 0} aria-label={`Dia do horário ${i + 1}`}>
                        {WEEKDAYS.map((label, day) => (
                          <option key={label} value={day}>
                            {label}
                          </option>
                        ))}
                      </select>
                    </td>
                    <td>
                      <input
                        name={`win_start_${i}`}
                        type="time"
                        defaultValue={window?.start ?? ""}
                        aria-label={`Início do horário ${i + 1}`}
                      />
                    </td>
                    <td>
                      <input
                        name={`win_end_${i}`}
                        type="time"
                        defaultValue={window?.end ?? ""}
                        aria-label={`Fim do horário ${i + 1}`}
                      />
                    </td>
                    <td className={local.center}>
                      <input
                        type="checkbox"
                        name={`win_pickup_${i}`}
                        defaultChecked={window ? window.modes.includes("pickup") : true}
                        aria-label={`Retirada no horário ${i + 1}`}
                      />
                    </td>
                    <td className={local.center}>
                      <input
                        type="checkbox"
                        name={`win_delivery_${i}`}
                        defaultChecked={window ? window.modes.includes("delivery") : true}
                        aria-label={`Entrega no horário ${i + 1}`}
                      />
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableWrap>
          <p className={styles.hint}>
            Uma linha por faixa de horário. Linha sem início ou fim é ignorada: apague os horários para tirar uma
            linha. Sempre aparecem duas linhas vazias; para mais, salve e continue.
          </p>
        </Section>

        <div className={local.saveBar}>
          <p>Retirada, entrega e horários são salvos juntos.</p>
          <button type="submit" className={styles.button}>
            Salvar entrega
          </button>
        </div>
      </form>

      {features.delivery ? (
        <UberSection
          tenantId={context.tenant_id}
          status={uber}
          owner={uberOwner}
          reader={uberReader}
          origin={origin}
          autoDispatch={cfg.delivery?.uber?.auto_dispatch ?? true}
          maxFee={cfg.delivery?.uber?.max_fee_cents ?? null}
          zone={context.timezone}
        />
      ) : null}

      {features.checkout ? (
        <Section title="Checkout" description="Prazo para pagar, cancelamento e devoluções">
          <form action={saveCheckout}>
            {tenantField}
            <div className={styles.fields}>
              <label className={styles.field}>
                Prazo para pagar (minutos)
                <input
                  name="pix_ttl_minutes"
                  type="number"
                  min={5}
                  max={1440}
                  defaultValue={checkout.pix_ttl_minutes ?? 30}
                />
                <span className={styles.fieldHint}>Depois disso, o pedido sem pagamento expira (5 a 1440).</span>
              </label>
              <label className={styles.field}>
                Pedidos em aberto por cliente
                <input
                  name="max_open_orders"
                  type="number"
                  min={1}
                  max={10}
                  defaultValue={checkout.max_open_orders ?? 3}
                />
                <span className={styles.fieldHint}>Quantos pedidos esperando pagamento cada cliente pode ter (1 a 10).</span>
              </label>
              <label className={styles.field}>
                Cliente cancela pedido pago até
                <select name="customer_cancel_until" defaultValue={checkout.customer_cancel_until ?? "accepted"}>
                  <option value="payment_confirmed">pagamento confirmado</option>
                  <option value="accepted">pedido aceito</option>
                </select>
                <span className={styles.fieldHint}>Depois disso, só a loja cancela.</span>
              </label>
              <label className={styles.field}>
                Reembolso acima de (R$) pede segunda aprovação
                <input
                  name="refund_threshold"
                  inputMode="decimal"
                  defaultValue={moneyInput(checkout.refund_four_eyes_threshold_cents ?? 20000)}
                />
                <span className={styles.fieldHint}>Acima desse valor, outra pessoa da loja precisa aprovar a devolução.</span>
              </label>
              <label className={`${styles.check} ${styles.fieldWide}`}>
                <input
                  type="checkbox"
                  name="require_whatsapp"
                  defaultChecked={checkout.require_whatsapp ?? false}
                />
                <span className={local.checkText}>
                  WhatsApp do cliente obrigatório no checkout
                  <span className={styles.fieldHint}>
                    Para você falar com o cliente sobre o pedido. Desligado, o campo continua lá, opcional.
                  </span>
                </span>
              </label>
              <label className={`${styles.check} ${styles.fieldWide}`}>
                <input type="checkbox" name="auto_accept" defaultChecked={checkout.auto_accept ?? false} />
                <span className={local.checkText}>
                  Aceitar pedidos pagos automaticamente
                  <span className={styles.fieldHint}>Sem isso, você aceita cada pedido pago na aba Pedidos.</span>
                </span>
              </label>
            </div>
            <div className={styles.formActions}>
              <button type="submit" className={styles.button}>
                Salvar checkout
              </button>
            </div>
          </form>
        </Section>
      ) : null}
    </>
  );
}
