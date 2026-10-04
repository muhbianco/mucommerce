/**
 * Medidas como a lojista digita (centímetros e quilos com vírgula) ↔ como a API guarda
 * (milímetros e gramas inteiros). Puro, para caber no vitest.
 *
 * A tela antiga pedia milímetros, e "30" virava 3 cm: a caixa cotada saía dez vezes menor e a
 * diferença aparecia no despacho. Aqui a unidade é a do dia a dia, e os avisos de sanidade pegam
 * o erro de digitação que sobra.
 */

const NUMERO = /^\d+(?:[.,]\d+)?$/;

function toNumber(raw: string): number | null {
  const limpo = raw.trim().replace(/\s/g, "");
  if (!limpo) return null;
  if (!NUMERO.test(limpo)) return Number.NaN;
  return Number(limpo.replace(",", "."));
}

/** "30" ou "30,5" cm → 300 ou 305 mm. Vazio → null; texto que não é número → NaN. */
export function parseCm(raw: string): number | null {
  const cm = toNumber(raw);
  if (cm === null || Number.isNaN(cm)) return cm;
  return Math.round(cm * 10);
}

/** 305 mm → "30,5" (para o value do input). */
export function cmInput(mm: number | null | undefined): string {
  if (mm === null || mm === undefined) return "";
  const cm = mm / 10;
  return Number.isInteger(cm) ? String(cm) : cm.toFixed(1).replace(".", ",");
}

/** [300, 200, 150] → "30 × 20 × 15 cm". */
export function dimsLabel(mms: readonly (number | null | undefined)[]): string {
  if (!mms.length || mms.some((mm) => !mm)) return "";
  return `${mms.map((mm) => cmInput(mm)).join(" × ")} cm`;
}

export type WeightUnit = "g" | "kg";

/** Peso digitado na unidade escolhida → gramas inteiros (arredonda para cima: frete não erra a menos). */
export function parseWeight(raw: string, unit: WeightUnit): number | null {
  const valor = toNumber(raw);
  if (valor === null || Number.isNaN(valor)) return valor;
  return Math.ceil(unit === "kg" ? valor * 1000 : valor);
}

/** Gramas → valor e unidade do formulário: abaixo de 1 kg em gramas, acima em quilos. */
export function weightInput(grams: number | null | undefined): { value: string; unit: WeightUnit } {
  if (grams === null || grams === undefined) return { value: "", unit: "g" };
  if (grams < 1000) return { value: String(grams), unit: "g" };
  const kg = grams / 1000;
  return { value: String(kg).replace(".", ","), unit: "kg" };
}

/** 150 → "150 g"; 1250 → "1,25 kg". */
export function weightLabel(grams: number | null | undefined): string {
  if (grams === null || grams === undefined) return "";
  if (grams < 1000) return `${grams} g`;
  return `${(grams / 1000).toLocaleString("pt-BR", { maximumFractionDigits: 3 })} kg`;
}

export type MeasureWarning = "lado_enorme" | "acima_30kg" | "lado_minusculo";

/**
 * Avisos que não bloqueiam, mostrados depois de salvar. O erro mais comum é de unidade: quem
 * pensava em milímetros escreve 300 e ganha uma caixa de 3 metros.
 */
export function measureWarnings(weightGrams: number | null, dimsMm: readonly (number | null)[]): MeasureWarning[] {
  const avisos: MeasureWarning[] = [];
  const lados = dimsMm.filter((mm): mm is number => typeof mm === "number" && mm > 0);
  if (lados.some((mm) => mm > 1500)) avisos.push("lado_enorme");
  if (lados.some((mm) => mm < 5)) avisos.push("lado_minusculo");
  if ((weightGrams ?? 0) > 30_000) avisos.push("acima_30kg");
  return avisos;
}

export const MEASURE_WARNING_TEXT: Record<MeasureWarning, string> = {
  lado_enorme: "Algum lado passa de 1,5 m. Confira se não digitou em milímetros (30 cm se escreve 30).",
  lado_minusculo: "Algum lado tem menos de meio centímetro. Confira a unidade: o campo é em centímetros.",
  acima_30kg: "Acima de 30 kg os Correios não levam; sobram poucas transportadoras na cotação.",
};
