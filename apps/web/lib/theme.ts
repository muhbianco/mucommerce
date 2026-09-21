/** Store theme from the tenant's branding: CSS variables for the storefront shell. */

const FONTS = {
  system: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  serif: 'Georgia, "Times New Roman", serif',
  rounded: 'ui-rounded, "SF Pro Rounded", "Nunito", system-ui, sans-serif',
} as const;

const HEX = /^#[0-9a-fA-F]{6}$/;

/** Black or white, whichever reads better on `hex` (WCAG relative luminance). */
export function onColor(hex: string): "#000000" | "#ffffff" {
  if (!HEX.test(hex)) return "#ffffff";
  const channel = (i: number) => {
    const c = parseInt(hex.slice(i, i + 2), 16) / 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  const luminance = 0.2126 * channel(1) + 0.7152 * channel(3) + 0.0722 * channel(5);
  // Contrast with black vs with white: pick the larger.
  return (luminance + 0.05) / 0.05 > 1.05 / (luminance + 0.05) ? "#000000" : "#ffffff";
}

export interface Branding {
  primary_color?: string;
  secondary_color?: string | null;
  font?: keyof typeof FONTS;
}

export function themeVariables(branding: Branding): Record<string, string> {
  const primary = branding.primary_color && HEX.test(branding.primary_color) ? branding.primary_color : "#111111";
  const secondary =
    branding.secondary_color && HEX.test(branding.secondary_color) ? branding.secondary_color : primary;
  return {
    "--brand-primary": primary,
    "--brand-on-primary": onColor(primary),
    "--brand-secondary": secondary,
    "--brand-font": FONTS[branding.font ?? "system"] ?? FONTS.system,
  };
}
