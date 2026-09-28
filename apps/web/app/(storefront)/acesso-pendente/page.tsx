import type { Metadata } from "next";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE, safeStorePath } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";

import { PhoneConfirm } from "../_store/phone-confirm";
import { StoreShell } from "../_store/store-shell";
import { requestAccess } from "./actions";

export const metadata: Metadata = { robots: { index: false, follow: false } };

interface Access {
  status: "approved" | "pending" | "blocked" | "revoked" | "none";
  requested_at: string | null;
}

const NOTICES: Record<string, string> = {
  pedido: "Pedido enviado. A loja avisa quando liberar.",
  limite: "Muitos pedidos em pouco tempo. Tente mais tarde.",
  erro: "Não foi possível enviar o pedido agora.",
};

export default async function PendingAccessPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string; ok?: string; tel?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { next, ok, tel } = await searchParams;
  const target = safeStorePath(next ?? "/loja");
  if (!(await cookies()).get(CUSTOMER_SESSION_COOKIE)) redirect(`/entrar?next=${encodeURIComponent(target)}`);

  let access: Access;
  try {
    access = await customerApi<Access>("/me/access");
  } catch (error) {
    if (error instanceof CustomerApiError && error.status === 401) {
      redirect(`/entrar?next=${encodeURIComponent(target)}&erro=sessao_expirada`);
    }
    throw error;
  }
  if (access.status === "approved" || context.access_mode !== "whitelist") redirect(target);

  return (
    <StoreShell context={context}>
      <h1>Acesso a {context.tenant.name}</h1>
      {ok && NOTICES[ok] ? <p role="status">{NOTICES[ok]}</p> : null}
      {access.status === "pending" ? (
        <p>Seu pedido de acesso está em análise pela loja.</p>
      ) : access.status === "blocked" ? (
        <p>Seu acesso a esta loja não está liberado. Fale com a loja se achar que é um engano.</p>
      ) : (
        <>
          <p>O catálogo desta loja é liberado para clientes aprovados. Peça acesso abaixo.</p>
          <form action={requestAccess}>
            <input type="hidden" name="next" value={target} />
            <label>
              Mensagem para a loja (opcional)
              <textarea name="message" maxLength={500} rows={3} />
            </label>
            <button type="submit" className="button">
              Solicitar acesso
            </button>
          </form>
        </>
      )}
      <PhoneConfirm context={context} back={`/acesso-pendente?next=${encodeURIComponent(target)}`} error={tel} />
      <form action="/auth/sair" method="post">
        <button type="submit" className="muted">
          Sair
        </button>
      </form>
    </StoreShell>
  );
}
