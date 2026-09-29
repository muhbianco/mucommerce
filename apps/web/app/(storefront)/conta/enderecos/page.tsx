import type { Metadata } from "next";
import Link from "next/link";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";

import { Breadcrumb, EmptyState, Notice, PageHead, Section } from "../../_store/ui";
import { StoreShell } from "../../_store/store-shell";
import styles from "../../_store/store.module.css";
import { deleteAddress, saveAddress } from "../actions";
import { CepField } from "./cep-field";
import local from "./enderecos.module.css";

export const metadata: Metadata = { title: "Meus endereços", robots: { index: false, follow: false } };

interface Address {
  id: string;
  label: string | null;
  recipient_name: string;
  phone: string | null;
  postal_code: string;
  street: string;
  number: string;
  complement: string | null;
  district: string;
  city: string;
  state: string;
  reference: string | null;
  is_default: boolean;
}

const OK: Record<string, string> = {
  criado: "Endereço salvo.",
  salvo: "Endereço atualizado.",
  apagado: "Endereço apagado.",
};
const ERRORS: Record<string, string> = {
  validation_error: "Confira os campos: CEP com 8 dígitos, UF válida e telefone com DDD.",
  conflict: "Você já tem 10 endereços nesta loja.",
  not_found: "Endereço não encontrado.",
  access_pending: "Sua conta ainda aguarda liberação da loja.",
};

/** Para onde a pessoa volta depois de salvar. Só caminho interno: um `next` de fora daqui seria
 *  um redirecionamento aberto de brinde. */
function safeNext(value: string | undefined): string | null {
  return value && /^\/[A-Za-z0-9\-/_]*$/.test(value) ? value : null;
}

function cep(value: string): string {
  return `${value.slice(0, 5)}-${value.slice(5)}`;
}

export default async function AddressesPage({
  searchParams,
}: {
  searchParams: Promise<{ ok?: string; erro?: string; editar?: string; next?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context || !context.features.checkout) notFound();
  const { ok, erro, editar, next } = await searchParams;
  const volta = safeNext(next);
  const here = `/conta/enderecos${volta ? `?next=${encodeURIComponent(volta)}` : ""}`;
  if (!(await cookies()).get(CUSTOMER_SESSION_COOKIE)) redirect(`/entrar?next=${encodeURIComponent(here)}`);

  let addresses: Address[];
  try {
    addresses = await customerApi<Address[]>("/me/addresses");
  } catch (error) {
    if (error instanceof CustomerApiError && error.status === 401) {
      redirect(`/entrar?next=${encodeURIComponent(here)}`);
    }
    if (error instanceof CustomerApiError && error.status === 403) {
      redirect(`/acesso-pendente?next=${encodeURIComponent(here)}`);
    }
    throw error;
  }
  const editing = addresses.find((address) => address.id === editar);

  return (
    <StoreShell context={context}>
      <Breadcrumb trail={[{ name: "Minha conta", href: "/conta" }, { name: "Meus endereços" }]} />
      <PageHead
        title="Meus endereços"
        lead="Onde a gente entrega. Você pode ter mais de um e escolher na hora da compra."
        actions={
          volta ? (
            <Link className={styles.buttonGhost} href={volta}>
              ← Voltar
            </Link>
          ) : null
        }
      />
      {ok && OK[ok] ? (
        <Notice kind="ok" role="status">
          {OK[ok]}
        </Notice>
      ) : null}
      {erro ? <Notice kind="warn" role="alert">{ERRORS[erro] ?? "Não foi possível salvar. Tente de novo."}</Notice> : null}

      {addresses.length === 0 ? (
        <EmptyState title="Você ainda não cadastrou nenhum endereço.">
          Preencha o formulário abaixo. Com o CEP, a gente completa quase tudo.
        </EmptyState>
      ) : (
        <div className={local.list}>
          {addresses.map((address) => (
            <article
              key={address.id}
              className={local.card}
              data-editing={address.id === editing?.id ? "" : undefined}
            >
              <div className={local.cardHead}>
                <strong>{address.label ?? address.recipient_name}</strong>
                {address.is_default ? <span className={local.badge}>padrão</span> : null}
              </div>
              <p className={local.cardBody}>
                {address.street}, {address.number}
                {address.complement ? ` — ${address.complement}` : ""}
                <br />
                {address.district} · {address.city}/{address.state}
                <br />
                CEP {cep(address.postal_code)}
                {address.recipient_name !== address.label ? (
                  <>
                    <br />
                    Recebe: {address.recipient_name}
                    {address.phone ? ` · ${address.phone}` : ""}
                  </>
                ) : null}
              </p>
              <div className={local.cardActions}>
                <Link
                  className={styles.buttonGhost}
                  href={`/conta/enderecos?editar=${address.id}${volta ? `&next=${encodeURIComponent(volta)}` : ""}`}
                >
                  Editar
                </Link>
                <form action={deleteAddress}>
                  <input type="hidden" name="address_id" value={address.id} />
                  <button type="submit" className={local.remove}>
                    Apagar
                  </button>
                </form>
              </div>
            </article>
          ))}
        </div>
      )}

      <Section
        variant="card"
        id="formulario"
        title={editing ? "Editar endereço" : "Novo endereço"}
        description={editing ? `Mexendo em “${editing.label ?? editing.recipient_name}”` : undefined}
        actions={
          // A saída da edição. Sem ela, quem clicou em "editar" ficava preso: o formulário virava
          // o de edição e não havia caminho de volta para cadastrar outro.
          editing ? (
            <Link className={styles.buttonGhost} href={here}>
              Cancelar e cadastrar outro
            </Link>
          ) : null
        }
      >
        <form action={saveAddress} id="form-endereco" className={styles.checkoutForm} key={editing?.id ?? "novo"}>
          {editing ? <input type="hidden" name="address_id" value={editing.id} /> : null}
          {volta ? <input type="hidden" name="next" value={volta} /> : null}

          <div className={local.row}>
            <label className={styles.field}>
              Nome de quem recebe
              <input
                name="recipient_name"
                required
                maxLength={120}
                autoComplete="name"
                defaultValue={editing?.recipient_name ?? ""}
              />
            </label>
            <label className={styles.field}>
              Telefone
              <input
                name="phone"
                inputMode="tel"
                maxLength={20}
                autoComplete="tel"
                placeholder="11 99999-9999"
                defaultValue={editing?.phone ?? ""}
              />
            </label>
            <label className={styles.field}>
              Apelido
              <input name="label" maxLength={40} placeholder="Casa, Trabalho" defaultValue={editing?.label ?? ""} />
            </label>
          </div>

          <div className={local.cep}>
            <CepField defaultValue={editing ? cep(editing.postal_code) : ""} />
          </div>

          <div className={local.rowStreet}>
            <label className={styles.field}>
              Rua
              <input
                name="street"
                required
                maxLength={160}
                autoComplete="address-line1"
                defaultValue={editing?.street ?? ""}
              />
            </label>
            <label className={styles.field}>
              Número
              <input name="number" required maxLength={20} defaultValue={editing?.number ?? ""} />
            </label>
            <label className={styles.field}>
              Complemento
              <input
                name="complement"
                maxLength={80}
                placeholder="apto, bloco"
                defaultValue={editing?.complement ?? ""}
              />
            </label>
          </div>

          <div className={local.rowCity}>
            <label className={styles.field}>
              Bairro
              <input name="district" required maxLength={80} defaultValue={editing?.district ?? ""} />
            </label>
            <label className={styles.field}>
              Cidade
              <input name="city" required maxLength={80} defaultValue={editing?.city ?? ""} />
            </label>
            <label className={styles.field}>
              UF
              <input
                name="state"
                required
                maxLength={2}
                size={3}
                style={{ textTransform: "uppercase" }}
                defaultValue={editing?.state ?? ""}
              />
            </label>
          </div>

          <label className={styles.field}>
            Ponto de referência
            <input
              name="reference"
              maxLength={160}
              placeholder="perto da praça, portão azul"
              defaultValue={editing?.reference ?? ""}
            />
          </label>

          <label className={styles.check}>
            <input
              type="checkbox"
              name="is_default"
              defaultChecked={editing?.is_default ?? addresses.length === 0}
            />
            Usar como endereço padrão
          </label>

          <div className={local.formActions}>
            <button type="submit" className="button">
              {editing ? "Salvar alterações" : "Salvar endereço"}
            </button>
            {editing ? (
              <Link className={styles.buttonGhost} href={here}>
                Cancelar
              </Link>
            ) : null}
          </div>
        </form>
      </Section>
    </StoreShell>
  );
}
