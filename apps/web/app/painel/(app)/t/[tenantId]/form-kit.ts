import "server-only";

import { redirect } from "next/navigation";

import { ApiError } from "@/lib/panel/api";
import { normalizeEmail, normalizeInstagram, normalizeWhatsapp } from "@/lib/panel/contact";
import { parseMoney } from "@/lib/panel/format";

// Shared by the panel's Server Action files: Server Actions only accept same-origin POSTs
// (CSRF), the API authorises every call (membership + scope + flag), and outcomes come back as
// ?ok=/?erro= so pages work without client JavaScript. Ids from forms are shape-checked before
// they are placed in an API path. ("use server" files may only export async functions, so
// these helpers live here.)

const ID = /^[0-9a-f-]{36}$/;

export class FormError extends Error {
  constructor(readonly code: string) {
    super(code);
  }
}

export function text(form: FormData, name: string): string {
  const value = form.get(name);
  return typeof value === "string" ? value.trim() : "";
}

export function optional(form: FormData, name: string): string | null {
  return text(form, name) || null;
}

export function id(value: string): string {
  if (!ID.test(value)) throw new FormError("id_invalido");
  return value;
}

export function tenantBase(form: FormData): { path: string; page: string } {
  const tenantId = id(text(form, "tenant_id"));
  return { path: `/admin/tenants/${tenantId}`, page: `/t/${tenantId}` };
}

export function money(form: FormData, name: string, { required = false } = {}): number | null {
  const cents = parseMoney(text(form, name));
  if (cents === null && required) throw new FormError("preco_obrigatorio");
  if (Number.isNaN(cents)) throw new FormError("preco_invalido");
  return cents;
}

const CONTACT_FIELDS = [
  ["whatsapp_e164", normalizeWhatsapp, "whatsapp_invalido"],
  ["instagram", normalizeInstagram, "instagram_invalido"],
  ["email", normalizeEmail, "email_invalido"],
] as const;

/**
 * Contato como a pessoa digita, guardado como o esquema exige (E.164, perfil sem "@", e-mail
 * minúsculo — ver `lib/panel/contact`).
 *
 * O campo que não dá para ler nomeia o próprio erro, em vez de devolver a página com "algum campo
 * está inválido", que obriga a lojista a caçar qual. Campo vazio vira `null`: é assim que ela
 * apaga um contato que não usa mais.
 */
export function contactFields(form: FormData): Record<string, string | null> {
  const out: Record<string, string | null> = {};
  for (const [name, normalize, code] of CONTACT_FIELDS) {
    const raw = optional(form, name);
    if (raw === null) {
      out[name] = null;
      continue;
    }
    const value = normalize(raw);
    if (value === null) throw new FormError(code);
    out[name] = value;
  }
  return out;
}

const REASON = /^[a-z_]{1,40}$/;

function outcome(error: unknown): string {
  if (error instanceof FormError) return `erro=${error.code}`;
  if (error instanceof ApiError) {
    // Quando a API diz *qual* regra quebrou (`details.reason`), a tela mostra a frase daquela
    // regra ("o produto teria de encolher para menos da metade"), não "algum campo é inválido".
    const reason = error.details.reason;
    if (typeof reason === "string" && REASON.test(reason)) return `erro=${error.code}.${reason}`;
    return `erro=${error.code}`;
  }
  throw error;
}

export async function run(back: string, ok: string, work: () => Promise<string | void>): Promise<never> {
  let target = `${back}${back.includes("?") ? "&" : "?"}ok=${ok}`;
  try {
    const next = await work();
    if (next) target = next;
  } catch (error) {
    target = `${back}${back.includes("?") ? "&" : "?"}${outcome(error)}`;
  }
  redirect(target);
}
