import type { Metadata } from "next";
import { redirect } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";
import { panelOrigin } from "@/lib/storefront";

export const metadata: Metadata = {
  title: "Validar ingresso",
  robots: { index: false, follow: false },
};

/**
 * O endereço que o QR carrega — e que só repassa para a tela de quem trabalha na porta.
 *
 * Esta rota existe por causa da marca (ADR 0020, decisão 6). O QR está impresso no ingresso do
 * cliente, então o endereço dentro dele precisa ser o da loja: quem comprar ingresso do show da
 * Lunares e escanear o próprio QR por curiosidade tem de ver `lunares.com.br`, não o domínio de
 * uma plataforma que nunca ouviu falar.
 *
 * Validar, porém, é ferramenta de funcionário: mora no painel, onde a sessão com permissão da
 * loja já existe. Montar sessão de painel no host da vitrine seria inventar um segundo jeito de
 * autenticar — e um segundo jeito de errar — para economizar um salto que ninguém vê.
 */
export default async function ValidateRedirect({
  params,
}: {
  params: Promise<{ code: string }>;
}) {
  const context = await getStorefrontContext();
  const { code } = await params;
  const limpo = code.replace(/[^A-Za-z0-9-]/g, "").slice(0, 32);
  if (!context) redirect("/");
  redirect(`${panelOrigin(context)}/t/${context.tenant.id}/validar/${limpo}`);
}
