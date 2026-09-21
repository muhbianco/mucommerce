import type { Metadata } from "next";

export const metadata: Metadata = { robots: { index: false, follow: false } };

export default function UnavailablePage() {
  // Neutral on purpose, like not-found: the middleware serves it with 503 + Retry-After
  // for a suspended store or when the API cannot be reached.
  return (
    <main>
      <h1>Loja temporariamente indisponível</h1>
      <p className="muted">Tente novamente em alguns minutos.</p>
    </main>
  );
}
