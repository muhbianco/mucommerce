import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";

import { StoreShell } from "../../_store/store-shell";
import { PageHead } from "../../_store/ui";
import { confirmSignup } from "../actions";

export const metadata: Metadata = {
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

/**
 * O link do e-mail cai aqui. Confirmar é um botão (POST), não a simples abertura do link:
 * antivírus de e-mail abrem os links para inspecionar, e um GET que gastasse o link entregaria a
 * sessão ao robô.
 */
export default async function ConfirmPage({ searchParams }: { searchParams: Promise<{ k?: string }> }) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { k } = await searchParams;
  return (
    <StoreShell context={context}>
      <PageHead title="Confirmar e-mail" />
      {k ? (
        <form action={confirmSignup}>
          <input type="hidden" name="k" value={k.slice(0, 120)} />
          <p>Falta um clique para entrar em {context.tenant.name}.</p>
          <button type="submit" className="button">
            Confirmar e entrar
          </button>
        </form>
      ) : (
        <p>Este link está incompleto. Abra de novo o link do e-mail.</p>
      )}
    </StoreShell>
  );
}
