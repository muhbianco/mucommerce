"use server";

import { api } from "@/lib/panel/api";

import { FormError, id, run, tenantBase, text } from "../form-kit";

// "https://Loja.Empresa.com.br/" → "loja.empresa.com.br": as pessoas colam a URL inteira.
function hostname(form: FormData): string {
  const raw = text(form, "hostname")
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .replace(/[/?#].*$/, "")
    .replace(/\.+$/, "");
  if (!/^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(raw)) {
    throw new FormError("dominio_invalido");
  }
  return raw;
}

export async function addDomain(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/dominios`, "dominio_criado", async () => {
    await api(`${path}/domains`, { method: "POST", json: { hostname: hostname(form) } });
  });
}

export async function verifyDomain(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/dominios`, "dominio_conferido", async () => {
    const domainId = id(text(form, "domain_id"));
    const check = await api<{ txt_ok: boolean; target_ok: boolean }>(
      `${path}/domains/${domainId}/verify`,
      { method: "POST" },
    );
    // O resultado da conferência é o que a pessoa quer ler, não um "salvo".
    if (check.txt_ok && check.target_ok) return `${page}/dominios?ok=dominio_ativo`;
    if (check.txt_ok) return `${page}/dominios?erro=dns_falta_apontar`;
    return `${page}/dominios?erro=dns_sem_txt`;
  });
}

export async function makePrimary(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/dominios`, "dominio_principal", async () => {
    await api(`${path}/domains/${id(text(form, "domain_id"))}/primary`, { method: "POST" });
  });
}

export async function disableDomain(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/dominios`, "dominio_desativado", async () => {
    await api(`${path}/domains/${id(text(form, "domain_id"))}`, { method: "DELETE" });
  });
}
