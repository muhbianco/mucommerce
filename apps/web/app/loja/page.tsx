import { notFound } from "next/navigation";

import { getStorefrontContext } from "@/lib/server-context";

export default async function StorePage() {
  const context = await getStorefrontContext();
  if (!context) notFound();
  return (
    <main>
      <h1>{context.tenant.name}</h1>
      <p className="muted">Vitrine protegida. Catálogo, carrinho e checkout chegam nas fases 1 e 2.</p>
    </main>
  );
}
