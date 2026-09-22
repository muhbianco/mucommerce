import type { Metadata } from "next";
import Link from "next/link";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";

import { StoreShell } from "../../_store/store-shell";
import { deleteAddress, saveAddress } from "../actions";

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

function cep(value: string): string {
  return `${value.slice(0, 5)}-${value.slice(5)}`;
}

export default async function AddressesPage({
  searchParams,
}: {
  searchParams: Promise<{ ok?: string; erro?: string; editar?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context || !context.features.checkout) notFound();
  if (!(await cookies()).get(CUSTOMER_SESSION_COOKIE)) redirect("/entrar?next=%2Fconta%2Fenderecos");
  let addresses: Address[];
  try {
    addresses = await customerApi<Address[]>("/me/addresses");
  } catch (error) {
    if (error instanceof CustomerApiError && error.status === 401) redirect("/entrar?next=%2Fconta%2Fenderecos");
    if (error instanceof CustomerApiError && error.status === 403) redirect("/acesso-pendente?next=%2Fconta%2Fenderecos");
    throw error;
  }
  const { ok, erro, editar } = await searchParams;
  const editing = addresses.find((address) => address.id === editar);

  return (
    <StoreShell context={context}>
      <p>
        <Link href="/conta">← Minha conta</Link>
      </p>
      <h1>Meus endereços</h1>
      {ok && OK[ok] ? <p role="status">{OK[ok]}</p> : null}
      {erro ? <p role="alert">{ERRORS[erro] ?? "Não foi possível salvar. Tente de novo."}</p> : null}
      {addresses.length === 0 ? <p>Nenhum endereço ainda.</p> : null}
      <ul>
        {addresses.map((address) => (
          <li key={address.id}>
            <strong>{address.label ?? address.recipient_name}</strong>
            {address.is_default ? " (padrão)" : ""} — {address.street}, {address.number}
            {address.complement ? ` ${address.complement}` : ""} · {address.district} · {address.city}/{address.state} ·{" "}
            {cep(address.postal_code)} <Link href={`/conta/enderecos?editar=${address.id}`}>editar</Link>{" "}
            <form action={deleteAddress} style={{ display: "inline" }}>
              <input type="hidden" name="address_id" value={address.id} />
              <button type="submit" className="muted">
                apagar
              </button>
            </form>
          </li>
        ))}
      </ul>

      <h2>{editing ? "Editar endereço" : "Novo endereço"}</h2>
      <form action={saveAddress} key={editing?.id ?? "novo"}>
        {editing ? <input type="hidden" name="address_id" value={editing.id} /> : null}
        <p>
          <label>
            Nome de quem recebe <input name="recipient_name" required maxLength={120} defaultValue={editing?.recipient_name ?? ""} />
          </label>
        </p>
        <p>
          <label>
            Telefone <input name="phone" inputMode="tel" maxLength={20} defaultValue={editing?.phone ?? ""} />
          </label>{" "}
          <label>
            Apelido (Casa, Trabalho) <input name="label" maxLength={40} defaultValue={editing?.label ?? ""} />
          </label>
        </p>
        <p>
          <label>
            CEP <input name="postal_code" required inputMode="numeric" maxLength={9} defaultValue={editing ? cep(editing.postal_code) : ""} />
          </label>
        </p>
        <p>
          <label>
            Rua <input name="street" required maxLength={160} defaultValue={editing?.street ?? ""} />
          </label>{" "}
          <label>
            Número <input name="number" required maxLength={20} defaultValue={editing?.number ?? ""} />
          </label>{" "}
          <label>
            Complemento <input name="complement" maxLength={80} defaultValue={editing?.complement ?? ""} />
          </label>
        </p>
        <p>
          <label>
            Bairro <input name="district" required maxLength={80} defaultValue={editing?.district ?? ""} />
          </label>{" "}
          <label>
            Cidade <input name="city" required maxLength={80} defaultValue={editing?.city ?? ""} />
          </label>{" "}
          <label>
            UF <input name="state" required maxLength={2} size={3} defaultValue={editing?.state ?? ""} />
          </label>
        </p>
        <p>
          <label>
            Referência <input name="reference" maxLength={160} defaultValue={editing?.reference ?? ""} />
          </label>
        </p>
        <p>
          <label>
            <input type="checkbox" name="is_default" defaultChecked={editing?.is_default ?? addresses.length === 0} /> Usar
            como padrão
          </label>
        </p>
        <button type="submit" className="button">
          Salvar endereço
        </button>
      </form>
    </StoreShell>
  );
}
