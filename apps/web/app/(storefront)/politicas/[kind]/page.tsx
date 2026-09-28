import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";

import { StoreShell } from "../../_store/store-shell";

const KINDS = { termos: "terms", privacidade: "privacy" } as const;
const TITLES = { terms: "Termos de uso", privacy: "Política de privacidade" } as const;

interface LegalDocument {
  kind: "terms" | "privacy";
  version: number;
  content: string;
  published_at: string;
}

export async function generateMetadata({ params }: { params: Promise<{ kind: string }> }): Promise<Metadata> {
  const kind = KINDS[(await params).kind as keyof typeof KINDS];
  return kind ? { title: TITLES[kind], robots: { index: false, follow: true } } : {};
}

/** The store's current terms/privacy, as plain text (paragraphs split on blank lines). */
export default async function PolicyPage({ params }: { params: Promise<{ kind: string }> }) {
  const context = await getStorefrontContext();
  if (!context) notFound();
  const kind = KINDS[(await params).kind as keyof typeof KINDS];
  if (!kind) notFound();
  const result = await storefrontApi<LegalDocument>(context, `/policies/${kind}`, {}, { anonymous: true });
  return (
    <StoreShell context={context}>
      <h1>{TITLES[kind]}</h1>
      {result.kind === "ok" ? (
        <>
          <p className="muted">
            Versão {result.data.version}, publicada em{" "}
            {new Date(result.data.published_at).toLocaleDateString("pt-BR")}.
          </p>
          {result.data.content.split(/\n\s*\n/).map((paragraph, index) => (
            <p key={index} style={{ whiteSpace: "pre-line" }}>
              {paragraph}
            </p>
          ))}
        </>
      ) : (
        <p className="muted">{context.tenant.name} ainda não publicou este documento.</p>
      )}
    </StoreShell>
  );
}
