"use server";

import { api } from "@/lib/panel/api";

import { FormError, run, tenantBase, text } from "../form-kit";

const KEY = /^[a-z_]+(\.[a-z_]+)?$/;

/**
 * Liga e desliga os módulos que não cobram. Os pagos nem chegam aqui: a API recusa com
 * `module_not_self_service`, e é ela que manda — esta tela só esconde o botão.
 */
export async function toggleModule(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const key = text(form, "key");
  if (!KEY.test(key)) throw new FormError("id_invalido");
  const enable = text(form, "enable") === "1";
  await run(`${page}/modulos`, enable ? "modulo_ligado" : "modulo_desligado", async () => {
    await api(`${path}/modules`, { method: "PUT", json: { flags: { [key]: enable } } });
  });
}

/** Quem enxerga a vitrine: todo mundo, quem tem conta, ou só quem você aprovou. */
export async function setAccessMode(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const mode = text(form, "access_mode");
  if (!["public", "login_required", "whitelist"].includes(mode)) throw new FormError("id_invalido");
  await run(`${page}/modulos`, "vitrine", async () => {
    await api(`${path}/storefront/access`, { method: "PUT", json: { access_mode: mode } });
  });
}
