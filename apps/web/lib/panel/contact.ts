// The landing "contact" block is typed by hand, so the panel accepts what people actually type
// and normalises it to what the API's schema requires (E.164, a bare Instagram handle, a
// lowercase e-mail). `null` means "no way to read this as one" — the caller turns that into the
// message for the field. Keeping these pure keeps them testable: the Server Action file may only
// export async functions.

/** "(11) 99999-9999", "11999999999", "+55 11 99999-9999" → "+5511999999999". */
export function normalizeWhatsapp(value: string): string | null {
  const digits = value.replace(/\D/g, "");
  if (!digits) return null;
  // Only a local number (10–11 digits, no "+") gets Brazil's country code.
  const withCountry = value.trim().startsWith("+") || digits.length > 11 ? digits : `55${digits}`;
  const e164 = `+${withCountry}`;
  return /^\+[1-9][0-9]{7,14}$/.test(e164) ? e164 : null;
}

/** "@loja", "https://instagram.com/loja/" → "loja". */
export function normalizeInstagram(value: string): string | null {
  const handle = value
    .trim()
    .replace(/^https?:\/\//i, "")
    .replace(/^(www\.)?instagram\.com\//i, "")
    .replace(/^@/, "")
    .replace(/[/?#].*$/, "");
  return /^[A-Za-z0-9._]{1,30}$/.test(handle) ? handle : null;
}

export function normalizeEmail(value: string): string | null {
  const address = value.trim().toLowerCase();
  if (address.length > 254) return null;
  return /^[^@\s]+@[^@\s]+\.[^@\s]+$/.test(address) ? address : null;
}
