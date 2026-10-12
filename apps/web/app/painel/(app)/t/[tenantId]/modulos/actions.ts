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
  await run(
    `${page}/modulos`,
    enable ? "modulo_ligado" : "modulo_desligado",
    async () => {
      await api(`${path}/modules`, {
        method: "PUT",
        json: { flags: { [key]: enable } },
      });
    },
  );
}

/**
 * Quem enxerga a vitrine (docs/14-pedidos-lunares.md §3): aberta, fechada com a página inicial
 * aberta, ou toda fechada. `login_required` só volta para quem já estava nele.
 */
const ACCESS_CHOICES: Record<
  string,
  { access_mode: string; landing_locked: boolean }
> = {
  public: { access_mode: "public", landing_locked: false },
  home_open: { access_mode: "whitelist", landing_locked: false },
  closed: { access_mode: "whitelist", landing_locked: true },
  login_required: { access_mode: "login_required", landing_locked: false },
};

export async function setAccessMode(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const choice = ACCESS_CHOICES[text(form, "access_choice")];
  if (!choice) throw new FormError("id_invalido");
  await run(`${page}/modulos`, "vitrine", async () => {
    await api(`${path}/storefront/access`, { method: "PUT", json: choice });
  });
}

/** Define ou troca a senha da loja fechada. Trocar tranca de novo quem entrou com a anterior. */
export async function setStorePassword(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  const password = text(form, "store_password").trim();
  if (password.length < 4 || password.length > 64)
    throw new FormError("senha_loja_curta");
  await run(`${page}/modulos`, "senha_loja_salva", async () => {
    await api(`${path}/storefront/password`, {
      method: "PUT",
      json: { password },
    });
  });
}

export async function removeStorePassword(form: FormData): Promise<void> {
  const { path, page } = tenantBase(form);
  await run(`${page}/modulos`, "senha_loja_removida", async () => {
    await api(`${path}/storefront/password`, { method: "DELETE" });
  });
}
