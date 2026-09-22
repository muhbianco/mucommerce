import "server-only";

import { redirect } from "next/navigation";

import { ApiError } from "@/lib/panel/api";
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

function outcome(error: unknown): string {
  if (error instanceof FormError || error instanceof ApiError) return `erro=${error.code}`;
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
