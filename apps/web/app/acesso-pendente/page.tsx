import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";

export default async function PendingAccessPage() {
  const context = await getStorefrontContext();
  if (!context) notFound();
  return (
    <main>
      <h1>Acesso pendente</h1>
      <p className="muted">
        Sua conta ainda não foi liberada para comprar em {context.tenant.name}. A equipe da loja
        aprova o acesso pelo atendimento; você recebe um e-mail quando isso acontecer.
      </p>
    </main>
  );
}
