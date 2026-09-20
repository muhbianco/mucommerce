import type { Metadata } from "next";
import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";

export async function generateMetadata(): Promise<Metadata> {
  const context = await getStorefrontContext();
  if (!context) return {};
  const seo = context.seo as { title?: string; description?: string; og_image_url?: string };
  const canonical = context.primary_host ? `https://${context.primary_host}/` : undefined;
  return {
    title: seo.title ?? context.tenant.name,
    description: seo.description ?? `Loja ${context.tenant.name}`,
    alternates: canonical ? { canonical } : undefined,
    openGraph: seo.og_image_url ? { images: [seo.og_image_url] } : undefined,
    robots: context.tenant.status === "active" ? { index: true, follow: true } : { index: false },
  };
}

export default async function LandingPage() {
  const context = await getStorefrontContext();
  if (!context) notFound();

  const branding = context.branding as { primary_color?: string; logo_url?: string | null };

  return (
    <main style={{ ["--brand-primary" as string]: branding.primary_color ?? "#111111" }}>
      {branding.logo_url ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img src={branding.logo_url} alt={context.tenant.name} height={64} />
      ) : (
        <h1>{context.tenant.name}</h1>
      )}
      <p className="muted">
        Landing configurável do tenant <strong>{context.tenant.slug}</strong>. Blocos (hero, destaques,
        eventos, sobre, contato) chegam na fase 1.
      </p>
      <p>
        <a className="button" href="/loja">
          Entrar na loja
        </a>
      </p>
      <p className="muted" style={{ fontSize: 12 }}>
        Acesso: {context.access_mode} · Fuso: {context.tenant.timezone} · Moeda: {context.tenant.currency}
      </p>
    </main>
  );
}
