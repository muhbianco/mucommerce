/** Pure formatting/parsing helpers for panel forms (no Next imports: tested with vitest). */

const BRL = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

export function formatMoney(cents: number | null | undefined): string {
  return cents === null || cents === undefined ? "—" : BRL.format(cents / 100);
}

/** Cents as the value of a price input: 1250 → "12,50". */
export function moneyInput(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "";
  return (cents / 100).toFixed(2).replace(".", ",");
}

/**
 * "12,50", "12.50", "1.234,56", "R$ 7" → cents. Empty → null. Anything else → NaN, so the
 * caller can tell "cleared" from "invalid".
 */
export function parseMoney(raw: string): number | null {
  let text = raw.replace(/R\$|\s/g, "");
  if (!text) return null;
  if (text.includes(",")) text = text.replace(/\./g, "").replace(",", ".");
  if (!/^\d+(\.\d{1,2})?$/.test(text)) return Number.NaN;
  return Math.round(Number(text) * 100);
}

/**
 * One modifier per line, "Nome = 3,50" (no price: free). Returns null when a line is not in
 * that shape or its price is not money.
 */
export function parseModifierLines(raw: string): { name: string; price_cents: number }[] | null {
  const items: { name: string; price_cents: number }[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const [name = "", price = "", ...rest] = line.split("=");
    if (rest.length || !name.trim()) return null;
    const cents = parseMoney(price);
    if (Number.isNaN(cents)) return null;
    items.push({ name: name.trim(), price_cents: cents ?? 0 });
  }
  return items;
}

/** One CEP range per line, "01000-000 a 01099-999" (or "-", "até"); null on a line it cannot read. */
export function parseCepRanges(raw: string): { start: string; end: string }[] | null {
  const ranges: { start: string; end: string }[] = [];
  for (const line of raw.split(/\r?\n/)) {
    if (!line.trim()) continue;
    const ceps = line.match(/\d{5}-?\d{3}/g);
    if (!ceps || ceps.length !== 2) return null;
    const [start, end] = ceps.map((cep) => cep.replace("-", "")) as [string, string];
    ranges.push({ start, end });
  }
  return ranges;
}

/** Non-empty trimmed lines (districts, one per line). */
export function lines(raw: string): string[] {
  return raw
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
}

/** Offset (minutes east of UTC) of `timeZone` at `instant`. */
function offsetMinutes(instant: Date, timeZone: string): number {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone,
    hourCycle: "h23",
    year: "numeric",
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
  }).formatToParts(instant);
  const get = (type: string) => Number(parts.find((p) => p.type === type)?.value);
  const asUtc = Date.UTC(get("year"), get("month") - 1, get("day"), get("hour"), get("minute"), get("second"));
  return Math.round((asUtc - instant.getTime()) / 60000);
}

/** `<input type="datetime-local">` value in the tenant's zone → ISO UTC. Empty → null. */
export function localToUtcIso(local: string, timeZone: string): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})$/.exec(local.trim());
  if (!match) return local.trim() ? "invalid" : null;
  const [, y, mo, d, h, mi] = match.map(Number) as [number, number, number, number, number, number];
  const guess = new Date(Date.UTC(y, mo - 1, d, h, mi));
  const utc = new Date(guess.getTime() - offsetMinutes(guess, timeZone) * 60000);
  return utc.toISOString();
}

/** ISO UTC → `<input type="datetime-local">` value in the tenant's zone. */
export function utcToLocalInput(iso: string | null | undefined, timeZone: string): string {
  if (!iso) return "";
  const instant = new Date(iso);
  const local = new Date(instant.getTime() + offsetMinutes(instant, timeZone) * 60000);
  return local.toISOString().slice(0, 16);
}

/** Decimal string from the API ("12.500") → "12,5" for display. */
export function formatQuantity(value: string, unit: string): string {
  const number = Number(value);
  const text = Number.isInteger(number)
    ? String(number)
    : number.toLocaleString("pt-BR", { maximumFractionDigits: 3 });
  return `${text} ${unit}`;
}

/** Quantity typed with a comma → API decimal string. Empty/invalid → null. */
export function parseQuantity(raw: string): string | null {
  const text = raw.trim().replace(",", ".");
  return /^-?\d+(\.\d{1,3})?$/.test(text) ? text : null;
}
