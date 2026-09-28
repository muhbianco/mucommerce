/**
 * Matemática de cor da vitrine: sRGB, contraste WCAG e OKLCH.
 *
 * Por que OKLCH e não uma mistura simples em sRGB: misturar canal a canal não é uniforme em
 * claridade. Um tom 50% de amarelo e um tom 50% de azul acabam com claridades percebidas muito
 * diferentes — e é exatamente a claridade que usamos como *garantia* de contraste. Em OKLCH,
 * fixar o L entrega contraste por construção, seja qual for a cor que o lojista escolher.
 *
 * Tudo devolve hexadecimal, nunca `color-mix()`: a maior parte do tráfego de loja é celular, com
 * cauda longa de WebView antigo, e hexadecimal funciona em tudo.
 *
 * Sem dependência: são duas matrizes e uma raiz cúbica.
 */

export interface Oklch {
  /** Claridade percebida, 0 (preto) a 1 (branco). */
  l: number;
  /** Croma: 0 é cinza; ~0.37 é o mais vívido que o sRGB alcança. */
  c: number;
  /** Matiz em graus, 0–360. Indefinido quando `c` é ~0, e nesse caso vale 0. */
  h: number;
}

const HEX = /^#[0-9a-fA-F]{6}$/;

/** Croma abaixo disto é cinza: o matiz de `atan2` vira ruído e não pode entrar na conta. */
const GRAY = 1e-6;

function clamp01(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

/** sRGB 0..1 por canal, ou `null` se o texto não for um hexadecimal de 6 dígitos. */
export function parseHex(hex: string): [number, number, number] | null {
  if (!HEX.test(hex)) return null;
  return [
    parseInt(hex.slice(1, 3), 16) / 255,
    parseInt(hex.slice(3, 5), 16) / 255,
    parseInt(hex.slice(5, 7), 16) / 255,
  ];
}

export function toHex(rgb: readonly [number, number, number]): string {
  const channel = (value: number) =>
    Math.round(clamp01(value) * 255)
      .toString(16)
      .padStart(2, "0");
  return `#${channel(rgb[0])}${channel(rgb[1])}${channel(rgb[2])}`;
}

/** "17 17 17", para escrever `rgb(var(--x) / 35%)` no CSS. */
export function rgbTriplet(hex: string): string {
  const rgb = parseHex(hex) ?? [0, 0, 0];
  return rgb.map((c) => Math.round(c * 255)).join(" ");
}

function toLinear(c: number): number {
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function fromLinear(c: number): number {
  return c <= 0.0031308 ? c * 12.92 : 1.055 * c ** (1 / 2.4) - 0.055;
}

/** Luminância relativa da WCAG. Cor inválida conta como preta (o caso pessimista). */
export function luminance(hex: string): number {
  const rgb = parseHex(hex);
  if (!rgb) return 0;
  const [r, g, b] = rgb.map(toLinear) as [number, number, number];
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

/** Razão de contraste da WCAG, de 1 (iguais) a 21 (preto contra branco). */
export function contrast(a: string, b: string): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

export function toOklch(hex: string): Oklch {
  const rgb = parseHex(hex);
  if (!rgb) return { l: 0, c: 0, h: 0 };
  const [r, g, b] = rgb.map(toLinear) as [number, number, number];

  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);

  const lightness = 0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s;
  const aAxis = 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s;
  const bAxis = 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s;

  const chroma = Math.hypot(aAxis, bAxis);
  // Cinza não tem matiz. Sem esta guarda, `atan2(0, 0)` devolve lixo que vira NaN no CSS e a
  // loja fica sem cor nenhuma.
  const hue = chroma < GRAY ? 0 : ((Math.atan2(bAxis, aAxis) * 180) / Math.PI + 360) % 360;
  return { l: lightness, c: chroma, h: hue };
}

/** OKLCH → sRGB linear, sem recortar: serve para saber se a cor cabe no gamute. */
function oklchToLinear(color: Oklch): [number, number, number] {
  const radians = (color.h * Math.PI) / 180;
  const aAxis = color.c * Math.cos(radians);
  const bAxis = color.c * Math.sin(radians);

  const l = (color.l + 0.3963377774 * aAxis + 0.2158037573 * bAxis) ** 3;
  const m = (color.l - 0.1055613458 * aAxis - 0.0638541728 * bAxis) ** 3;
  const s = (color.l - 0.0894841775 * aAxis - 1.291485548 * bAxis) ** 3;

  return [
    4.0767416621 * l - 3.3077115913 * m + 0.2309699292 * s,
    -1.2684380046 * l + 2.6097574011 * m - 0.3413193965 * s,
    -0.0041960863 * l - 0.7034186147 * m + 1.707614701 * s,
  ];
}

function inGamut(rgb: readonly [number, number, number]): boolean {
  // Uma folga de meio passo de 8 bits: recortar isso é invisível e evita rejeitar a própria cor
  // que acabou de ser convertida de hexadecimal por erro de ponto flutuante.
  return rgb.every((c) => c >= -0.001 && c <= 1.001);
}

/**
 * OKLCH → hexadecimal. L e C arbitrários caem fora do sRGB, então o croma é reduzido por busca
 * binária até a cor caber, mantendo claridade e matiz — que é o que sustenta o contraste.
 */
export function fromOklch(color: Oklch): string {
  const l = clamp01(color.l);
  const h = ((color.h % 360) + 360) % 360;
  let low = 0;
  let high = Math.max(color.c, 0);
  if (inGamut(oklchToLinear({ l, c: high, h }))) {
    return toHex(oklchToLinear({ l, c: high, h }).map(fromLinear) as [number, number, number]);
  }
  for (let i = 0; i < 12; i += 1) {
    const mid = (low + high) / 2;
    if (inGamut(oklchToLinear({ l, c: mid, h }))) low = mid;
    else high = mid;
  }
  return toHex(oklchToLinear({ l, c: low, h }).map(fromLinear) as [number, number, number]);
}

/** A mesma cor com outra claridade. É a ferramenta que gera a rampa da marca. */
export function withLightness(hex: string, lightness: number): string {
  const { c, h } = toOklch(hex);
  return fromOklch({ l: lightness, c, h });
}

/** Mistura em OKLab (t = 0 devolve `a`, t = 1 devolve `b`). */
export function mix(a: string, b: string, t: number): string {
  const first = toOklch(a);
  const second = toOklch(b);
  const amount = clamp01(t);
  // Interpola pelo caminho curto do círculo de matiz; um cinza herda o matiz do outro lado.
  const fromHue = first.c < GRAY ? second.h : first.h;
  const toHue = second.c < GRAY ? first.h : second.h;
  let delta = toHue - fromHue;
  if (delta > 180) delta -= 360;
  if (delta < -180) delta += 360;
  return fromOklch({
    l: first.l + (second.l - first.l) * amount,
    c: first.c + (second.c - first.c) * amount,
    h: fromHue + delta * amount,
  });
}

/**
 * A cor mais próxima de `hex`, no mesmo matiz, que atinge `ratio` contra `against`.
 *
 * É esta função que defende a loja da cor que o lojista escolheu: amarelo puro como cor de texto
 * sobre branco dá 1,3:1, e volta daqui escuro o bastante para ler.
 */
export function towardContrast(hex: string, against: string, ratio: number): string {
  if (contrast(hex, against) >= ratio) return hex;
  const { c, h } = toOklch(hex);
  // Contra fundo claro escurecemos; contra fundo escuro clareamos.
  const darken = luminance(against) > 0.18;
  let low = darken ? 0 : toOklch(hex).l;
  let high = darken ? toOklch(hex).l : 1;
  let best = darken ? "#000000" : "#ffffff";
  if (contrast(best, against) < ratio) return best; // nem o extremo chega: devolve o extremo
  for (let i = 0; i < 16; i += 1) {
    const mid = (low + high) / 2;
    const candidate = fromOklch({ l: mid, c, h });
    if (contrast(candidate, against) >= ratio) {
      best = candidate;
      if (darken) low = mid;
      else high = mid;
    } else if (darken) {
      high = mid;
    } else {
      low = mid;
    }
  }
  return best;
}
