import type { StoreImage as Image } from "@/lib/storefront";
import { srcSet } from "@/lib/storefront";

/** Responsive image from the WebP renditions; width/height keep layout stable (CLS). */
export function StoreImage({
  image,
  alt,
  sizes,
  className,
  priority = false,
}: {
  image: Image;
  alt: string;
  sizes: string;
  className?: string;
  priority?: boolean;
}) {
  const largest = image.renditions[image.renditions.length - 1];
  if (!largest) return null;
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img
      src={largest.url}
      srcSet={srcSet(image)}
      sizes={sizes}
      width={image.width ?? largest.width}
      height={image.height ?? largest.height}
      alt={image.alt ?? alt}
      className={className}
      loading={priority ? "eager" : "lazy"}
      decoding="async"
      fetchPriority={priority ? "high" : undefined}
    />
  );
}
