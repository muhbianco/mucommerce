import type { Metadata } from "next";
import Link from "next/link";
import { cookies } from "next/headers";
import { notFound, redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { CUSTOMER_SESSION_COOKIE } from "@/lib/customer-cookies";
import { getStorefrontContext } from "@/lib/server-context";

import { PhoneConfirm } from "../_store/phone-confirm";
import { StoreShell } from "../_store/store-shell";

export const metadata: Metadata = { title: "Minha conta", robots: { index: false, follow: false } };

interface Session {
  customer: { name: string | null; email_masked: string | null };
  access_status: string | null;
}

export default async function AccountPage({ searchParams }: { searchParams: Promise<{ tel?: string }> }) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  if (!(await cookies()).get(CUSTOMER_SESSION_COOKIE)) redirect("/entrar?next=%2Fconta");
  let session: Session;
  try {
    session = await customerApi<Session>("/me/session");
  } catch (error) {
    if (error instanceof CustomerApiError && error.status === 401) redirect("/entrar?next=%2Fconta&erro=sessao_expirada");
    throw error;
  }
  const { tel } = await searchParams;
  return (
    <StoreShell context={context}>
      <h1>Minha conta</h1>
      <p>
        {session.customer.name ?? "Cliente"}
        {session.customer.email_masked ? ` · ${session.customer.email_masked}` : null}
      </p>
      <PhoneConfirm context={context} back="/conta" error={tel} />
      {context.features.checkout ? (
        <p>
          <Link href="/conta/enderecos">Meus endereços</Link>
        </p>
      ) : null}
      <form action="/auth/sair" method="post">
        <button type="submit" className="muted">
          Sair
        </button>
      </form>
    </StoreShell>
  );
}
