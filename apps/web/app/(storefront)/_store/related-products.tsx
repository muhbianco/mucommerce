import { storefrontApi } from "@/lib/storefront-api";
import type { CategoryRef, ProductPage } from "@/lib/storefront";
import type { StorefrontContext } from "@/lib/tenant";

import { ProductCard } from "./product-card";
import { Grid, Section } from "./ui";

/** Quantos cabem numa faixa sem virar um segundo catálogo. */
const LIMIT = 8;

/**
 * "Mais de ⟨categoria⟩", abaixo do produto.
 *
 * É um componente assíncrono separado de propósito: dentro de um `<Suspense>`, a caixa de
 * compra pinta e este bloco chega depois, em vez de a página inteira esperar mais uma chamada
 * de até cinco segundos — na página que mais converte.
 *
 * Não é "quem viu também levou": não temos dado de co-visualização, e inventar isso seria
 * mentir na interface. É o que a loja tem na mesma prateleira, que é uma promessa que dá para
 * cumprir.
 */
export async function RelatedProducts({
  context,
  category,
  excludeId,
}: {
  context: StorefrontContext;
  category: CategoryRef | null;
  excludeId: string;
}) {
  const result = await storefrontApi<ProductPage>(context, "/catalog/products", {
    category: category?.slug,
    limit: String(LIMIT + 1), // +1 porque o próprio produto volta na lista
  });
  if (result.kind !== "ok") return null;

  const items = result.data.items.filter((p) => p.id !== excludeId).slice(0, LIMIT);
  // Uma vitrine com um item só não é uma prateleira; é um produto solto repetido.
  if (items.length < 2) return null;

  return (
    <Section title={category ? `Mais de ${category.name}` : "Outros produtos da loja"}>
      <Grid>
        {items.map((product) => (
          <ProductCard key={product.id} product={product} />
        ))}
      </Grid>
    </Section>
  );
}
