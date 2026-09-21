"use server";

import { cookies } from "next/headers";
import { redirect } from "next/navigation";

import { CustomerApiError, customerApi } from "@/lib/customer-api";
import { customerCookie, safeStorePath, WHATSAPP_LINK_COOKIE } from "@/lib/customer-cookies";

/**
 * "Confirmar WhatsApp", step 1: the API stores the challenge and answers with the wa.me link to
 * the official number ("CONFIRMAR <código>"). The link waits 10 minutes in a host-only cookie and
 * the page shows it as a plain link (step 2): a form POST redirected to wa.me would be blocked by
 * the CSP form-action, and the code stays out of our URLs. Server Actions are same-origin only.
 */
export async function startWhatsApp(form: FormData): Promise<void> {
  const back = safeStorePath(String(form.get("back") ?? "/conta"));
  const phone = String(form.get("phone") ?? "").slice(0, 32);
  const separator = back.includes("?") ? "&" : "?";
  let url: string;
  try {
    ({ whatsapp_url: url } = await customerApi<{ whatsapp_url: string }>("/me/phone/start", {
      json: { phone },
    }));
  } catch (error) {
    if (!(error instanceof CustomerApiError)) throw error;
    redirect(`${back}${separator}tel=${encodeURIComponent(error.code)}`);
  }
  if (!url.startsWith("https://wa.me/")) throw new Error("unexpected WhatsApp link");
  (await cookies()).set(WHATSAPP_LINK_COOKIE, url, customerCookie(600));
  redirect(back);
}
