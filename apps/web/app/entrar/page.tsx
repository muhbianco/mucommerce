import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";

export default async function LoginPage() {
  const context = await getStorefrontContext();
  if (!context) notFound();
  // Customer login (Google, callback central + handoff) arrives with the identity slice of
  // phase 1; until then there is no endpoint to link to.
  return (
    <main>
      <h1>Entrar em {context.tenant.name}</h1>
      <p className="muted">O acesso com login ainda não está disponível nesta loja.</p>
    </main>
  );
}
