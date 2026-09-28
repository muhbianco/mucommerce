/**
 * O tema da vitrine, derivado da marca do lojista.
 *
 * O lojista escolhe duas cores e uma fonte. Daqui sai a paleta inteira — superfícies, linhas,
 * texto, estados — com contraste calculado, não escolhido. Ele pode pedir amarelo puro; a loja
 * continua legível.
 *
 * A regra que sustenta tudo, e que alguém vai querer quebrar um dia:
 *
 *   `--brand-primary` só preenche superfície. Quem pinta glifo é `--store-brand-ink`.
 *
 * O jeito clássico de errar aqui não é texto em cima do botão (`onColor` resolve isso). É usar a
 * cor da marca como cor de *link* sobre fundo claro: #ffd400 sobre branco dá 1,3:1.
 */

import { contrast, fromOklch, rgbTriplet, toOklch, towardContrast, withLightness } from "./color";

/** Pares (corpo, título). O lojista responde uma pergunta, não duas. */
const FONTS = {
  system: 'system-ui, -apple-system, "Segoe UI", Roboto, sans-serif',
  serif: 'Georgia, "Times New Roman", serif',
  // `--store-font-rounded` vem do `next/font` (app/(storefront)/_store/fonts.ts). O resto da
  // pilha é a rede de segurança para antes da fonte carregar.
  rounded: 'var(--store-font-rounded), ui-rounded, "SF Pro Rounded", system-ui, sans-serif',
} as const;

export type StoreFont = keyof typeof FONTS;

const HEX = /^#[0-9a-fA-F]{6}$/;

/** Black or white, whichever reads better on `hex` (WCAG relative luminance). */
export function onColor(hex: string): "#000000" | "#ffffff" {
  if (!HEX.test(hex)) return "#ffffff";
  return contrast("#000000", hex) >= contrast("#ffffff", hex) ? "#000000" : "#ffffff";
}

export interface Branding {
  primary_color?: string;
  secondary_color?: string | null;
  font?: StoreFont;
}

/** Matiz fixo dos estados: verde, âmbar e vermelho, que ninguém lê de outro jeito. */
const OK_HUE = 152;
const WARN_HUE = 82;
const DANGER_HUE = 27;

/** Um estado a menos de tantos graus da marca confunde: "esgotado" some dentro do botão. */
const MIN_STATE_DISTANCE = 24;

function hueDistance(a: number, b: number): number {
  const delta = Math.abs(a - b) % 360;
  return delta > 180 ? 360 - delta : delta;
}

/** Afasta o matiz do estado quando a marca cai em cima dele. */
function separate(stateHue: number, brandHue: number): number {
  if (hueDistance(stateHue, brandHue) >= MIN_STATE_DISTANCE) return stateHue;
  const away = ((stateHue - brandHue + 540) % 360) - 180; // de que lado fugir
  return (stateHue + (away >= 0 ? MIN_STATE_DISTANCE : -MIN_STATE_DISTANCE) + 360) % 360;
}

export interface StoreTheme {
  brand: string;
  onBrand: string;
  brandHover: string;
  /** A marca como texto: a única que pode pintar letra, ícone ou preço. */
  brandInk: string;
  brandTint: string;
  onBrandTint: string;
  brandRgb: string;
  accent: string;
  accentInk: string;
  paper: string;
  surface: string;
  mist: string;
  line: string;
  muted: string;
  inkSoft: string;
  ink: string;
  inkRgb: string;
  ok: string;
  okTint: string;
  okInk: string;
  warn: string;
  warnTint: string;
  warnInk: string;
  danger: string;
  dangerTint: string;
  dangerInk: string;
}

function state(hue: number, brandHue: number): { base: string; tint: string; ink: string } {
  const h = separate(hue, brandHue);
  const base = fromOklch({ l: 0.52, c: 0.15, h });
  const tint = fromOklch({ l: 0.96, c: 0.045, h });
  return { base, tint, ink: towardContrast(base, tint, 4.5) };
}

/**
 * A paleta inteira a partir do que o lojista escolheu.
 *
 * Os neutros levam um tom da marca (croma mínimo, proporcional ao dela): loja de cor apagada
 * ganha cinza neutro, loja vibrante ganha um cinza da mesma família. As claridades são fixas,
 * e é isso que garante o contraste seja qual for a cor.
 */
export function storeTheme(branding: Branding): StoreTheme {
  const brand = branding.primary_color && HEX.test(branding.primary_color) ? branding.primary_color : "#111111";
  const secondary =
    branding.secondary_color && HEX.test(branding.secondary_color) ? branding.secondary_color : brand;

  const { l: brandL, c: brandC, h } = toOklch(brand);
  const tinge = (max: number, factor: number) => Math.min(max, brandC * factor);

  const paper = fromOklch({ l: 0.985, c: tinge(0.008, 0.12), h });
  const surface = "#ffffff"; // cartão branco: foto de produto não pode amarelar
  const mist = fromOklch({ l: 0.955, c: tinge(0.014, 0.18), h });
  const line = fromOklch({ l: 0.885, c: tinge(0.018, 0.2), h });
  const muted = fromOklch({ l: 0.545, c: tinge(0.02, 0.25), h });
  const inkSoft = fromOklch({ l: 0.4, c: tinge(0.025, 0.25), h });
  const ink = fromOklch({ l: 0.235, c: tinge(0.03, 0.3), h });

  const brandTint = fromOklch({ l: 0.965, c: tinge(0.045, 0.5), h });
  const brandInk = towardContrast(brand, surface, 4.5);

  const okState = state(OK_HUE, h);
  const warnState = state(WARN_HUE, h);
  const dangerState = state(DANGER_HUE, h);

  return {
    brand,
    onBrand: onColor(brand),
    // Marca escura não tem para onde escurecer: clareia, e com piso, senão preto puro fica com
    // um hover que ninguém enxerga.
    brandHover: withLightness(brand, brandL < 0.25 ? Math.max(brandL + 0.09, 0.24) : brandL * 0.9),
    brandInk,
    brandTint,
    onBrandTint: towardContrast(brand, brandTint, 4.5),
    brandRgb: rgbTriplet(brandInk),
    accent: secondary,
    accentInk: towardContrast(secondary, surface, 4.5),
    paper,
    surface,
    mist,
    line,
    muted,
    inkSoft,
    ink,
    inkRgb: rgbTriplet(ink),
    ok: okState.base,
    okTint: okState.tint,
    okInk: okState.ink,
    warn: warnState.base,
    warnTint: warnState.tint,
    warnInk: warnState.ink,
    danger: dangerState.base,
    dangerTint: dangerState.tint,
    dangerInk: dangerState.ink,
  };
}

/** As propriedades CSS que o shell da vitrine injeta inline. */
export function themeVariables(branding: Branding): Record<string, string> {
  const t = storeTheme(branding);
  return {
    // Contrato antigo: o painel e o e2e leem estes nomes.
    "--brand-primary": t.brand,
    "--brand-on-primary": t.onBrand,
    "--brand-secondary": t.accent,
    "--brand-font": FONTS[branding.font ?? "system"] ?? FONTS.system,
    // Derivados: tudo que o sistema calculou.
    "--store-brand-hover": t.brandHover,
    "--store-brand-ink": t.brandInk,
    "--store-brand-tint": t.brandTint,
    "--store-on-brand-tint": t.onBrandTint,
    "--store-brand-rgb": t.brandRgb,
    "--store-accent-ink": t.accentInk,
    "--store-paper": t.paper,
    "--store-surface": t.surface,
    "--store-mist": t.mist,
    "--store-line": t.line,
    "--store-muted": t.muted,
    "--store-ink-soft": t.inkSoft,
    "--store-ink": t.ink,
    "--store-ink-rgb": t.inkRgb,
    "--store-ok": t.ok,
    "--store-ok-tint": t.okTint,
    "--store-ok-ink": t.okInk,
    "--store-warn": t.warn,
    "--store-warn-tint": t.warnTint,
    "--store-warn-ink": t.warnInk,
    "--store-danger": t.danger,
    "--store-danger-tint": t.dangerTint,
    "--store-danger-ink": t.dangerInk,
  };
}
