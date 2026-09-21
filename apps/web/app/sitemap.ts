import type { MetadataRoute } from "next";

import { getStorefrontContext } from "@/lib/server-context";
import { storefrontApi } from "@/lib/storefront-api";
import { isIndexable, storeOrigin } from "@/lib/storefront";

export const dynamic = "force-dynamic";

interface SitemapData {
  products: { slug: string; updated_at: string }[];
  categories: string[];
}

/** Per-tenant sitemap (the middleware attached the tenant). Empty unless the store is indexable. */
export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const context = await getStorefrontContext();
  if (!context || !isIndexable(context)) return [];
  const origin = storeOrigin(context);
  const entries: MetadataRoute.Sitemap = [{ url: `${origin}/`, changeFrequency: "weekly", priority: 1 }];
  if (!context.features.catalog) return entries;
  const data = await storefrontApi<SitemapData>(context, "/catalog/sitemap");
  if (data.kind !== "ok") return entries;
  entries.push({ url: `${origin}/loja`, changeFrequency: "daily", priority: 0.9 });
  for (const slug of data.data.categories) {
    entries.push({ url: `${origin}/loja/categoria/${slug}`, changeFrequency: "weekly", priority: 0.6 });
  }
  for (const product of data.data.products) {
    entries.push({
      url: `${origin}/loja/produto/${product.slug}`,
      lastModified: product.updated_at,
      changeFrequency: "weekly",
      priority: 0.8,
    });
  }
  return entries;
}
