import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";

export default async function LoginPage({
  searchParams,
}: {
  searchParams: Promise<{ next?: string }>;
}) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const { next } = await searchParams;
  // Only relative paths are honoured as return targets.
  const returnTo = next && next.startsWith("/") && !next.startsWith("//") ? next : "/loja";
  return (
    <main>
      <h1>Entrar em {context.tenant.name}</h1>
      <p className="muted">
        O login Google (Authorization Code + PKCE, callback central) entra na fase 1. O botão abaixo
        aponta para o endpoint que iniciará o fluxo.
      </p>
      <p>
        <a className="button" href={`/api/v1/auth/google/start?return_to=${encodeURIComponent(returnTo)}`}>
          Entrar com Google
        </a>
      </p>
    </main>
  );
}
