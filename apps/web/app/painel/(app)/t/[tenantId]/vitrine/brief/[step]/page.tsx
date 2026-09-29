import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { api, requireMe } from "@/lib/panel/api";
import { tenantScopes } from "@/lib/panel/scopes";

import styles from "../../../../../../panel.module.css";
import { Flash } from "../../../flash";
import { PageHeader, Pill, Section } from "../../../ui";
import { saveBriefStep } from "../actions";
import {
  SEGMENTS,
  SERVES,
  STEPS,
  STEP_LEAD,
  STEP_TITLE,
  VOICES,
  audiencePlaceholder,
  isStep,
  sellsPlaceholder,
} from "../steps";
import local from "../brief.module.css";

export const metadata: Metadata = { title: "Sobre a sua loja" };

interface Brief {
  segment: string;
  segment_other: string | null;
  sells: string;
  audience: string | null;
  differentials: string[];
  voice: string;
  city: string | null;
  state: string | null;
  neighborhood: string | null;
  serves: string[];
  hours_note: string | null;
  whatsapp_e164: string | null;
  instagram: string | null;
  email: string | null;
  keywords: string[];
  avoid: string | null;
  references: string[];
  notes: string | null;
}

interface BriefState {
  brief: Brief;
  steps: string[];
  all_steps: string[];
  usable: boolean;
  quota: { period: string; used: number; limit: number; left: number; paid: boolean };
}

export default async function BriefStepPage({
  params,
  searchParams,
}: {
  params: Promise<{ tenantId: string; step: string }>;
  searchParams: Promise<{ ok?: string; erro?: string }>;
}) {
  const { tenantId, step } = await params;
  if (!isStep(step)) notFound();
  const me = await requireMe();
  if (!tenantScopes(me, tenantId).can("settings:write")) notFound();

  const base = `/t/${tenantId}`;
  const state = await api<BriefState>(`/admin/tenants/${tenantId}/landing/brief`);
  const brief = state.brief;
  const { ok, erro } = await searchParams;

  const at = STEPS.indexOf(step);
  const following = STEPS[at + 1];
  // Depois do último passo a lojista vai para a tela da página inicial, que é onde o botão de
  // gerar mora. O passo não some: ela pode voltar e mexer.
  const next = following ? `${base}/vitrine/brief/${following}` : `${base}/vitrine`;

  return (
    <>
      <PageHeader
        eyebrow="Vitrine"
        title={STEP_TITLE[step]}
        lead={STEP_LEAD[step]}
        actions={<Pill state={state.steps.length === STEPS.length ? "live" : "pending"}>{`${state.steps.length} de ${STEPS.length}`}</Pill>}
      />
      <Flash ok={ok} erro={erro} />

      <nav className={local.trail} aria-label="Passos">
        {STEPS.map((key, i) => (
          <a
            key={key}
            href={`${base}/vitrine/brief/${key}`}
            className={local.trailStep}
            aria-current={key === step ? "step" : undefined}
            data-done={state.steps.includes(key) ? "" : undefined}
          >
            {/* O número (ou o visto) é decoração: o nome do passo já identifica o link, e
                repetir "1" no leitor de tela só atrapalha. O estado vai no `.done`. */}
            <span className={local.trailNumber} aria-hidden="true">
              {state.steps.includes(key) ? "✓" : i + 1}
            </span>
            {STEP_TITLE[key]}
            {state.steps.includes(key) ? <span className={local.done}> — respondido</span> : null}
          </a>
        ))}
      </nav>

      <Section title={`Passo ${at + 1} de ${STEPS.length}`}>
        <form action={saveBriefStep} className={local.form}>
          <input type="hidden" name="tenant_id" value={tenantId} />
          <input type="hidden" name="step" value={step} />
          <input type="hidden" name="next" value={next} />

          {step === "negocio" ? (
            <>
              <label className={local.field}>
                <span>O que a sua loja é</span>
                <select name="segment" defaultValue={brief.segment}>
                  {SEGMENTS.map((s) => (
                    <option key={s.value} value={s.value}>
                      {s.label}
                    </option>
                  ))}
                </select>
              </label>
              <label className={local.field}>
                <span>
                  Se escolheu “Outro”, diga em duas palavras <em>(opcional)</em>
                </span>
                <input
                  name="segment_other"
                  maxLength={60}
                  defaultValue={brief.segment_other ?? ""}
                  placeholder="Ex.: ateliê de velas"
                />
              </label>
              <label className={local.field}>
                <span>O que você vende</span>
                <textarea
                  name="sells"
                  rows={3}
                  maxLength={400}
                  defaultValue={brief.sells}
                  placeholder={sellsPlaceholder(brief.segment)}
                />
                <small className={styles.hint}>
                  Escreva como você explicaria para um cliente novo. É a resposta que mais muda a
                  página.
                </small>
              </label>
            </>
          ) : null}

          {step === "publico" ? (
            <>
              <label className={local.field}>
                <span>
                  Quem compra de você <em>(opcional)</em>
                </span>
                <textarea
                  name="audience"
                  rows={2}
                  maxLength={300}
                  defaultValue={brief.audience ?? ""}
                  placeholder={audiencePlaceholder(brief.segment)}
                />
              </label>
              <label className={local.field}>
                <span>
                  Por que de você, e não de outro <em>(opcional)</em>
                </span>
                <textarea
                  name="differentials"
                  rows={5}
                  defaultValue={brief.differentials.join("\n")}
                  placeholder={"Uma por linha. Ex.:\nfeito no dia\nentrego de bicicleta no bairro\nembalagem para presente"}
                />
                <small className={styles.hint}>Uma por linha, até cinco. Viram os selos da página.</small>
              </label>
              <fieldset className={local.choices}>
                <legend>Como a sua loja fala</legend>
                {VOICES.map((v) => (
                  <label key={v.value} className={local.choice}>
                    <input type="radio" name="voice" value={v.value} defaultChecked={brief.voice === v.value} />
                    <span>
                      <strong>{v.label}</strong>
                      <small>{v.hint}</small>
                    </span>
                  </label>
                ))}
              </fieldset>
            </>
          ) : null}

          {step === "onde" ? (
            <>
              <div className={local.row}>
                <label className={local.field}>
                  <span>
                    Cidade <em>(opcional)</em>
                  </span>
                  <input name="city" maxLength={80} defaultValue={brief.city ?? ""} placeholder="Contagem" />
                </label>
                <label className={local.fieldShort}>
                  <span>UF</span>
                  <input
                    name="state"
                    maxLength={2}
                    defaultValue={brief.state ?? ""}
                    placeholder="MG"
                    style={{ textTransform: "uppercase" }}
                  />
                </label>
                <label className={local.field}>
                  <span>
                    Bairro <em>(opcional)</em>
                  </span>
                  <input
                    name="neighborhood"
                    maxLength={80}
                    defaultValue={brief.neighborhood ?? ""}
                    placeholder="Eldorado"
                  />
                </label>
              </div>
              <fieldset className={local.choices}>
                <legend>Como o cliente recebe</legend>
                {SERVES.map((s) => (
                  <label key={s.value} className={local.choice}>
                    <input
                      type="checkbox"
                      name="serves"
                      value={s.value}
                      defaultChecked={brief.serves.includes(s.value)}
                    />
                    <span>
                      <strong>{s.label}</strong>
                    </span>
                  </label>
                ))}
              </fieldset>
              <label className={local.field}>
                <span>
                  Horário, em uma linha <em>(opcional)</em>
                </span>
                <input
                  name="hours_note"
                  maxLength={200}
                  defaultValue={brief.hours_note ?? ""}
                  placeholder="Ex.: seg a sáb, 9h às 18h; encomenda com 2 dias"
                />
              </label>
              <div className={local.row}>
                <label className={local.field}>
                  <span>
                    WhatsApp <em>(opcional)</em>
                  </span>
                  <input
                    name="whatsapp_e164"
                    inputMode="tel"
                    defaultValue={brief.whatsapp_e164 ?? ""}
                    placeholder="11 99999-9999"
                  />
                </label>
                <label className={local.field}>
                  <span>
                    Instagram <em>(opcional)</em>
                  </span>
                  <input name="instagram" defaultValue={brief.instagram ?? ""} placeholder="sualoja" />
                </label>
                <label className={local.field}>
                  <span>
                    E-mail <em>(opcional)</em>
                  </span>
                  <input
                    name="email"
                    type="email"
                    defaultValue={brief.email ?? ""}
                    placeholder="contato@sualoja.com.br"
                  />
                </label>
              </div>
            </>
          ) : null}

          {step === "jeito" ? (
            <>
              <label className={local.field}>
                <span>
                  Palavras que você quer ver na página <em>(opcional)</em>
                </span>
                <input
                  name="keywords"
                  defaultValue={brief.keywords.join(", ")}
                  placeholder="artesanal, feito na hora, sem conservante"
                />
                <small className={styles.hint}>Separadas por vírgula, até oito.</small>
              </label>
              <label className={local.field}>
                <span>
                  O que a gente <strong>não</strong> deve escrever <em>(opcional)</em>
                </span>
                <textarea
                  name="avoid"
                  rows={2}
                  maxLength={200}
                  defaultValue={brief.avoid ?? ""}
                  placeholder="Ex.: não falar “o melhor preço da cidade”, não prometer entrega em 30 min"
                />
              </label>
              <label className={local.field}>
                <span>
                  Páginas de que você gosta, descritas <em>(opcional)</em>
                </span>
                <textarea
                  name="references"
                  rows={3}
                  defaultValue={brief.references.join("\n")}
                  placeholder={"Uma por linha. Ex.:\nfundo claro, foto grande e pouco texto\nmenu simples, só três opções"}
                />
                <small className={styles.hint}>
                  Descreva com palavras, não cole endereço de site: a gente não visita links, e
                  copiar a página de outra loja não ajudaria você.
                </small>
              </label>
              <label className={local.field}>
                <span>
                  Mais algo que a gente deva saber <em>(opcional)</em>
                </span>
                <textarea name="notes" rows={3} maxLength={600} defaultValue={brief.notes ?? ""} />
              </label>
            </>
          ) : null}

          <div className={local.actions}>
            <button type="submit" className={styles.button}>
              {following ? "Salvar e continuar" : "Salvar e terminar"}
            </button>
            {at > 0 ? (
              <a className={styles.buttonSmall} href={`${base}/vitrine/brief/${STEPS[at - 1]}`}>
                Voltar
              </a>
            ) : null}
            {/* Sair no meio é legítimo: o que já foi salvo fica. */}
            <a className={styles.buttonSmall} href={`${base}/vitrine`}>
              {state.usable ? "Pular e gerar com o que tem" : "Sair sem terminar"}
            </a>
          </div>
        </form>
      </Section>

      {state.usable ? null : (
        <p className={styles.hint}>
          Enquanto você não disser <strong>o que a loja vende</strong>, a gente não tem o que
          escrever — o resto das perguntas é bônus.
        </p>
      )}
    </>
  );
}
